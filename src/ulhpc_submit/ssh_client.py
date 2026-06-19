"""SSH/Paramiko client wrapper with retries and safe error mapping.

All access-node SSH work goes through this class. It is intentionally kept
thin; module/conda setup is not executed here (it runs inside the Slurm job).
"""

import os
import random
import shlex
import time
from typing import Optional, Tuple

import paramiko

from .errors import SyncNetworkError, ULHPCError


class SSHClient:
    """Wrapper around paramiko.SSHClient."""

    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        key_path: Optional[str] = None,
        key_passphrase: Optional[str] = None,
        max_retries: int = 1,
    ):
        self.host = host
        self.port = port
        self.user = user
        self.key_path = key_path
        self.key_passphrase = key_passphrase
        self.max_retries = max_retries
        self._client: Optional[paramiko.SSHClient] = None

    def _connect_once(self) -> paramiko.SSHClient:
        client = paramiko.SSHClient()
        client.load_system_host_keys()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        connect_kwargs: dict = {
            "hostname": self.host,
            "port": self.port,
            "username": self.user,
            "timeout": 20,
            "banner_timeout": 30,
        }
        if self.key_path:
            key_path = os.path.expanduser(self.key_path)
            connect_kwargs["key_filename"] = key_path
            if self.key_passphrase:
                connect_kwargs["passphrase"] = self.key_passphrase
        client.connect(**connect_kwargs)
        return client

    def connect(self) -> paramiko.SSHClient:
        """Connect with exponential backoff and jitter."""
        last_exc: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            try:
                self._client = self._connect_once()
                return self._client
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if attempt < self.max_retries:
                    sleep_seconds = min(2 ** attempt, 30) + random.uniform(0, 1)
                    time.sleep(sleep_seconds)
        raise SyncNetworkError(
            f"Could not SSH to {self.user}@{self.host}:{self.port} after {self.max_retries} attempts: {last_exc}",
        )

    def exec_command(self, command: str) -> Tuple[int, str, str]:
        """Execute a command on the access node.

        Returns (return_code, stdout, stderr).
        """
        if self._client is None:
            self.connect()
        assert self._client is not None
        stdin, stdout, stderr = self._client.exec_command(command)
        rc = stdout.channel.recv_exit_status()
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        return rc, out, err

    def expand_remote_path(self, remote_path: str) -> str:
        """Resolve a remote path to an absolute path, creating it if needed.

        SFTP does not expand '~', so we ask the remote shell to resolve the
        path before using it for SFTP operations.
        """
        cmd = f"bash -c 'mkdir -p {shlex.quote(remote_path)} && cd {shlex.quote(remote_path)} && pwd'"
        rc, out, err = self.exec_command(cmd)
        if rc != 0:
            raise SyncNetworkError(
                f"Could not resolve remote path {remote_path}: {err.strip()}"
            )
        return out.strip()

    def sftp_put(self, local_path: str, remote_path: str) -> None:
        """Upload a file via SFTP."""
        if self._client is None:
            self.connect()
        assert self._client is not None
        sftp = self._client.open_sftp()
        try:
            sftp.put(local_path, remote_path)
        finally:
            sftp.close()

    def sftp_get(self, remote_path: str, local_path: str) -> None:
        """Download a file via SFTP."""
        if self._client is None:
            self.connect()
        assert self._client is not None
        sftp = self._client.open_sftp()
        try:
            sftp.get(remote_path, local_path)
        finally:
            sftp.close()

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> "SSHClient":
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()
