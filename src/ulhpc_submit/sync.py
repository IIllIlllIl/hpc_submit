"""Code synchronization: rsync wrapper + remote integrity checks."""

import fnmatch
import os
import shlex
import subprocess
from pathlib import Path
from typing import Callable, List, Optional

from .errors import (
    SyncDiskFullError,
    SyncIntegrityError,
    SyncNetworkError,
    SyncPermissionError,
)
from .ssh_client import SSHClient


class CodeSync:
    """Sync a local directory to a remote directory over SSH/rsync."""

    def __init__(
        self,
        ssh: SSHClient,
        local_dir: str,
        remote_dir: str,
        excludes: Optional[List[str]] = None,
        rsync_cmd: Optional[Callable[..., subprocess.CompletedProcess]] = None,
    ):
        self.ssh = ssh
        self.local_dir = os.path.abspath(local_dir)
        self.remote_dir = remote_dir
        self.excludes = excludes or []
        self.rsync_cmd = rsync_cmd or subprocess.run

    def _remote_dir_status(self) -> tuple:
        """Return (exists, writable) by running tests on the access node."""
        quoted = shlex.quote(self.remote_dir)
        cmd = f"test -d {quoted} && echo EXISTS; test -w {quoted} && echo WRITABLE"
        rc, out, err = self.ssh.exec_command(cmd)
        exists = "EXISTS" in out
        writable = "WRITABLE" in out
        return exists, writable, rc, err

    def _ensure_remote_dir(self) -> None:
        """Create remote directory if missing."""
        rc, _, err = self.ssh.exec_command(f"mkdir -p {shlex.quote(self.remote_dir)}")
        if rc != 0:
            if "Permission denied" in err or "Permission" in err:
                raise SyncPermissionError(
                    f"Cannot create remote directory {self.remote_dir}: {err.strip()}"
                )
            if "No space" in err or "Disk quota" in err:
                raise SyncDiskFullError(
                    f"Cannot create remote directory {self.remote_dir}: {err.strip()}"
                )
            raise SyncNetworkError(
                f"Failed to create remote directory {self.remote_dir}: {err.strip()}"
            )

    def _check_disk_space(self) -> None:
        """Pre-flight check of remote disk usage."""
        rc, out, err = self.ssh.exec_command(f"df -h {shlex.quote(self.remote_dir)}")
        if rc != 0:
            return  # do not block sync if df is unavailable
        # df output like: Filesystem Size Used Avail Use% Mounted on
        lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
        if len(lines) >= 2:
            parts = lines[-1].split()
            if len(parts) >= 5:
                use_percent = parts[4].replace("%", "")
                try:
                    if int(use_percent) >= 99:
                        raise SyncDiskFullError(
                            f"Remote filesystem is {use_percent}% full for {self.remote_dir}."
                        )
                except ValueError:
                    pass

    def _build_rsync_cmd(self) -> List[str]:
        """Construct rsync command with excludes and SSH key."""
        ssh_options = f"ssh -p {self.ssh.port}"
        key_path = getattr(self.ssh, "key_path", None)
        if key_path:
            key_path = os.path.expanduser(key_path)
            ssh_options += f" -i {shlex.quote(key_path)}"
        cmd = [
            "rsync",
            "-avz",
            "--delete",
            "-e",
            ssh_options,
        ]
        for pattern in self.excludes:
            cmd.extend(["--exclude", pattern])
        # trailing slashes ensure directory contents are synced
        cmd.append(f"{self.local_dir}/")
        cmd.append(f"{self.ssh.user}@{self.ssh.host}:{self.remote_dir}/")
        return cmd

    def _is_excluded(self, name: str) -> bool:
        return any(fnmatch.fnmatch(name, pattern) for pattern in self.excludes)

    def _count_local_files(self) -> int:
        count = 0
        for root, dirs, files in os.walk(self.local_dir):
            # Do not descend into excluded directories.
            dirs[:] = [d for d in dirs if not self._is_excluded(d)]
            count += sum(1 for f in files if not self._is_excluded(f))
        return count

    def _count_remote_files(self) -> int:
        cmd = f"find {shlex.quote(self.remote_dir)} -type f | wc -l"
        rc, out, err = self.ssh.exec_command(cmd)
        if rc != 0:
            raise SyncIntegrityError(
                f"Could not verify remote file count: {err.strip()}"
            )
        try:
            return int(out.strip().split()[0])
        except (ValueError, IndexError) as exc:
            raise SyncIntegrityError(
                f"Unexpected remote file count output: {out!r}"
            ) from exc

    def sync(self) -> None:
        """Run full sync with checks.

        Raises a Sync* error subclass on failure so callers can map it to an
        error code and remediation suggestion.
        """
        exists, writable, _, err = self._remote_dir_status()
        if not exists:
            self._ensure_remote_dir()
        elif not writable:
            raise SyncPermissionError(
                f"Remote directory {self.remote_dir} is not writable."
            )

        self._check_disk_space()

        local_count = self._count_local_files()

        cmd = self._build_rsync_cmd()
        try:
            result = self.rsync_cmd(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
        except FileNotFoundError as exc:
            raise SyncNetworkError(
                "rsync executable not found. Please install rsync."
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise SyncNetworkError(
                f"rsync subprocess failed unexpectedly: {exc}"
            ) from exc

        if result.returncode != 0:
            stderr = (result.stderr or "").lower()
            if "permission denied" in stderr or "denied" in stderr:
                raise SyncPermissionError(
                    f"rsync permission denied: {result.stderr}"
                )
            if "no space" in stderr or "disk quota" in stderr:
                raise SyncDiskFullError(
                    f"rsync reported disk full: {result.stderr}"
                )
            if "connection" in stderr or "timeout" in stderr or "refused" in stderr:
                raise SyncNetworkError(
                    f"rsync network error: {result.stderr}"
                )
            raise SyncNetworkError(
                f"rsync failed with exit code {result.returncode}: {result.stderr}"
            )

        # Integrity check
        remote_count = self._count_remote_files()
        if remote_count != local_count:
            raise SyncIntegrityError(
                f"File count mismatch after sync: local={local_count}, remote={remote_count}."
            )

    def sync_dry_run(self) -> str:
        """Return the rsync command that would be executed."""
        return " ".join(self._build_rsync_cmd())
