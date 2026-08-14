"""Job submission via sbatch on the access node."""

import os
import re
import shlex
from pathlib import Path
from typing import Optional

from .errors import (
    JobAuthorizationError,
    JobInvalidAccountError,
    JobInvalidQOSError,
    JobInvalidResourcesError,
    JobSubmitError,
)


def classify_sbatch_error(stderr: str):
    """Return the most specific exception class for an sbatch failure."""
    text = stderr.lower()
    if "invalid qos" in text or "invalid qos specification" in text:
        return JobInvalidQOSError
    if "invalid account" in text and "account/partition" not in text:
        return JobInvalidAccountError
    if any(
        pattern in text
        for pattern in (
            "user's group not permitted to use this partition",
            "invalid account or account/partition combination",
            "not authorized to use this partition",
            "not permitted to use this partition",
        )
    ):
        return JobAuthorizationError
    if any(
        pattern in text
        for pattern in (
            "requested node configuration is not available",
            "invalid time limit",
            "requested time limit is invalid",
            "time limit specification",
            "invalid memory",
            "memory specification",
            "invalid generic resource",
            "more processors requested",
            "invalid number of cpus",
            "requested resources exceed",
            "request exceeds",
        )
    ):
        return JobInvalidResourcesError
    return JobSubmitError
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
            detail = (err or out).strip()
            error_class = classify_sbatch_error(detail)
            raise error_class(f"sbatch failed ({rc}): {detail}")

        match = re.search(r"Submitted batch job\s+(\d+)", out)
        if not match:
            raise JobSubmitError(
                f"Could not parse job ID from sbatch output: {out!r} {err!r}"
            )
        return match.group(1)

    def cancel(self, job_id: str) -> None:
        """Cancel a submitted job (useful for cleanup in tests)."""
        self.ssh.exec_command(f"scancel {shlex.quote(job_id)}")
