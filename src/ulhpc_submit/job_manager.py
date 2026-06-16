"""Job submission via sbatch on the access node."""

import os
import re
import shlex
from pathlib import Path
from typing import Optional

from .errors import JobInvalidResourcesError, JobSubmitError
from .ssh_client import SSHClient


class JobManager:
    """Upload a job script and submit it with sbatch."""

    def __init__(self, ssh: SSHClient, remote_dir: str):
        self.ssh = ssh
        self.remote_dir = remote_dir

    def upload_script(self, local_script_path: str, remote_name: Optional[str] = None) -> str:
        """Upload the generated Slurm script to the remote project directory."""
        remote_name = remote_name or "ulhpc_submit_job.sh"
        remote_path = f"{self.remote_dir}/{remote_name}"
        self.ssh.sftp_put(local_script_path, remote_path)
        # Make executable in case SFTP didn't preserve permissions
        self.ssh.exec_command(f"chmod +x {shlex.quote(remote_path)}")
        return remote_path

    def submit(self, remote_script_path: str) -> str:
        """Run sbatch and return the job ID."""
        rc, out, err = self.ssh.exec_command(f"sbatch {shlex.quote(remote_script_path)}")
        if rc != 0:
            stderr_lower = (err or "").lower()
            if any(
                keyword in stderr_lower
                for keyword in (
                    "invalid",
                    "exceeds",
                    "partition",
                    "memory",
                    "time limit",
                    "qos",
                    "cpu",
                    "gpu",
                    "node configuration",
                    "not available",
                    "exceeds",
                )
            ):
                raise JobInvalidResourcesError(
                    f"sbatch rejected resource request: {err.strip()}"
                )
            raise JobSubmitError(f"sbatch failed ({rc}): {err.strip()}")

        match = re.search(r"Submitted batch job\s+(\d+)", out)
        if not match:
            raise JobSubmitError(
                f"Could not parse job ID from sbatch output: {out!r} {err!r}"
            )
        return match.group(1)

    def cancel(self, job_id: str) -> None:
        """Cancel a submitted job (useful for cleanup in tests)."""
        self.ssh.exec_command(f"scancel {shlex.quote(job_id)}")
