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


def format_bytes(n: int) -> str:
    """Return a human-readable byte string (base-1024)."""
    if n < 0:
        raise ValueError("byte count must be non-negative")
    units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"]
    value = float(n)
    for unit in units:
        if value < 1024.0 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.2f} {unit}"
        value /= 1024.0
    return f"{value:.2f} {units[-1]}"


def compute_local_size(path: str, excludes: List[str]) -> int:
    """Sum file sizes under path, honouring exclude patterns."""

    def is_excluded(name: str) -> bool:
        return any(fnmatch.fnmatch(name, pattern) for pattern in excludes)

    total = 0
    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if not is_excluded(d)]
        for filename in files:
            if is_excluded(filename):
                continue
            try:
                total += os.path.getsize(os.path.join(root, filename))
            except OSError:
                continue
    return total


class CodeSync:
    """Sync a local directory to a remote directory over SSH/rsync."""

    @staticmethod
    def format_upload_size(local_dir: str, excludes: List[str]) -> str:
        return format_bytes(compute_local_size(local_dir, excludes))

    def __init__(
        self,
        ssh: SSHClient,
        local_dir: str,
        remote_dir: str,
        excludes: Optional[List[str]] = None,
        rsync_cmd: Optional[Callable[..., subprocess.CompletedProcess]] = None,
        progress: Optional[Callable[[str], None]] = None,
        free_space_margin: float = 1.0,
        remote_extra_policy: str = "strict",
    ):
        self.ssh = ssh
        self.local_dir = os.path.abspath(local_dir)
        self.remote_dir = remote_dir
        self.excludes = excludes or []
        self.rsync_cmd = rsync_cmd or subprocess.run
        self.progress = progress or (lambda _msg: None)
        self.free_space_margin = free_space_margin
        self.remote_extra_policy = remote_extra_policy

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
        """Pre-flight check of remote disk space against local upload size.

        Uses ``df -B1 -P`` for byte-accurate free space, computes the local
        directory size honouring excludes, and raises ``SyncDiskFullError``
        before rsync if the remote filesystem cannot accommodate the upload
        with the configured safety margin.
        """
        local_size = compute_local_size(self.local_dir, self.excludes)
        required = int(local_size * self.free_space_margin)

        rc, out, err = self.ssh.exec_command(
            f"df -B1 -P {shlex.quote(self.remote_dir)}"
        )
        if rc != 0:
            self.progress(
                "Could not determine remote free space; continuing sync."
            )
            return

        # POSIX df: header + one data line. Example:
        # Filesystem         1B-blocks     Used Available Use% Mounted on
        # /dev/sda1       10737418240 5679089   5058327  53% /home
        lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
        if len(lines) < 2:
            self.progress(
                "Remote free space output was unexpected; continuing sync."
            )
            return

        parts = lines[-1].split()
        # POSIX guarantees: fs, blocks, used, available, capacity, mountpoint
        if len(parts) < 5:
            self.progress(
                "Could not parse remote free space; continuing sync."
            )
            return

        try:
            total = int(parts[1])
            available = int(parts[3])
            use_percent = parts[4].rstrip("%")
        except (ValueError, IndexError):
            self.progress(
                "Could not parse remote free space; continuing sync."
            )
            return

        self.progress(
            f"Upload size: {format_bytes(local_size)}; "
            f"remote free: {format_bytes(available)} / {format_bytes(total)} "
            f"({use_percent}% used)"
        )

        if available < required:
            raise SyncDiskFullError(
                f"Insufficient disk space for sync to {self.remote_dir}: "
                f"upload requires {format_bytes(required)} "
                f"({self.free_space_margin}x margin), "
                f"but only {format_bytes(available)} is available "
                f"({use_percent}% used)."
            )

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
        return len(self._list_local_files())

    def _list_local_files(self) -> List[str]:
        files: List[str] = []
        for root, dirs, filenames in os.walk(self.local_dir):
            # Do not descend into excluded directories.
            dirs[:] = [d for d in dirs if not self._is_excluded(d)]
            for filename in filenames:
                if self._is_excluded(filename):
                    continue
                path = Path(root) / filename
                files.append(path.relative_to(self.local_dir).as_posix())
        return sorted(files)

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

    def _list_remote_files(self) -> List[str]:
        quoted = shlex.quote(self.remote_dir)
        cmd = f"cd {quoted} && find . -type f -print | sed 's#^./##' | sort"
        rc, out, err = self.ssh.exec_command(cmd)
        if rc != 0:
            raise SyncIntegrityError(
                f"Could not list remote files for integrity check: {err.strip()}"
            )
        return [line.strip() for line in out.splitlines() if line.strip()]

    def _format_integrity_mismatch(
        self,
        local_count: int,
        remote_count: int,
        extra: List[str],
        missing: List[str],
    ) -> str:
        parts = [
            f"File count mismatch after sync: local={local_count}, remote={remote_count}."
        ]
        if extra:
            shown = ", ".join(extra[:20])
            suffix = f" (+{len(extra) - 20} more)" if len(extra) > 20 else ""
            parts.append(f"Remote extra files: {shown}{suffix}.")
        if missing:
            shown = ", ".join(missing[:20])
            suffix = f" (+{len(missing) - 20} more)" if len(missing) > 20 else ""
            parts.append(f"Missing remote files: {shown}{suffix}.")
        if extra and self.excludes:
            parts.append(
                "Remote extra files may be leftovers from paths excluded by sync_excludes."
            )
        return " ".join(parts)

    def _safe_remote_file_path(self, relative_path: str) -> str:
        path = Path(relative_path)
        if path.is_absolute() or ".." in path.parts:
            raise SyncIntegrityError(
                f"Refusing to clean unsafe remote extra path: {relative_path}"
            )
        return f"{self.remote_dir}/{relative_path}"

    def _remove_remote_files(self, relative_paths: List[str]) -> None:
        if not relative_paths:
            return
        quoted = " ".join(
            shlex.quote(self._safe_remote_file_path(path)) for path in relative_paths
        )
        rc, _, err = self.ssh.exec_command(f"rm -f -- {quoted}")
        if rc != 0:
            raise SyncIntegrityError(
                f"Failed to clean remote extra files: {err.strip()}"
            )

    def _handle_integrity_mismatch(self, local_count: int, remote_count: int) -> None:
        local_files = set(self._list_local_files())
        remote_files = set(self._list_remote_files())
        extra = sorted(remote_files - local_files)
        missing = sorted(local_files - remote_files)

        if missing:
            raise SyncIntegrityError(
                self._format_integrity_mismatch(local_count, remote_count, extra, missing)
            )

        if extra and self.remote_extra_policy == "ignore":
            self.progress(
                f"Ignoring {len(extra)} remote extra file(s) after sync "
                "(--remote-ignore-extra)."
            )
            return

        if extra and self.remote_extra_policy == "clean":
            self.progress(
                f"Cleaning {len(extra)} remote extra file(s) after sync "
                "(--remote-clean-excluded)."
            )
            self._remove_remote_files(extra)
            return

        raise SyncIntegrityError(
            self._format_integrity_mismatch(local_count, remote_count, extra, missing)
        )

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
            self._handle_integrity_mismatch(local_count, remote_count)

    def sync_dry_run(self) -> str:
        """Return the rsync command that would be executed."""
        return " ".join(self._build_rsync_cmd())
