"""Shared pytest fixtures and helpers."""

import shutil
import subprocess
import shlex
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import pytest

from ulhpc_submit.config import Config


class FakeSSHClient:
    """In-memory SSH client that records commands and returns configured responses."""

    def __init__(
        self,
        host: str = "access-iris.uni.lu",
        port: int = 8022,
        user: str = "testuser",
        key_path: Optional[str] = None,
        key_passphrase: Optional[str] = None,
        max_retries: int = 5,
    ):
        self.host = host
        self.port = port
        self.user = user
        self.key_path = key_path
        self.connected = False
        self.commands: List[str] = []
        self.responses: Dict[str, Tuple[int, str, str]] = {}
        self.files: Dict[str, str] = {}  # remote path -> content
        self.connect_fail = False

    def connect(self):
        if self.connect_fail:
            raise ConnectionError("forced connection failure")
        self.connected = True

    def exec_command(self, command: str) -> Tuple[int, str, str]:
        self.commands.append(command)
        for pattern, (rc, out, err) in self.responses.items():
            if pattern in command:
                return rc, out, err
        # Sensible defaults for common probes
        if "test -d" in command and "-w" in command:
            return 0, "EXISTS\nWRITABLE\n", ""
        if "mkdir -p" in command:
            return 0, "", ""
        if command.startswith("rm -f -- "):
            try:
                for path in shlex.split(command)[3:]:
                    candidate = Path(path)
                    if candidate.exists() and candidate.is_file():
                        candidate.unlink()
            except ValueError:
                pass
            return 0, "", ""
        if "df -B1 -P" in command:
            return (
                0,
                f"Filesystem     1B-blocks        Used    Available Use% Mounted on\n"
                f"/dev/sda      107374182400   10737418240  96636764160  10% {command.split()[-1]}",
                "",
            )
        if "find . -type f -print" in command:
            try:
                parts = shlex.split(command)
                remote_dir = parts[1]
                root = Path(remote_dir)
                if root.exists():
                    files = sorted(
                        p.relative_to(root).as_posix()
                        for p in root.rglob("*")
                        if p.is_file()
                    )
                    return 0, "\n".join(files) + ("\n" if files else ""), ""
            except (ValueError, IndexError):
                pass
            return 0, "", ""
        if "find " in command and "wc -l" in command:
            try:
                parts = shlex.split(command)
                remote_dir = parts[1]
                root = Path(remote_dir)
                if root.exists():
                    count = sum(1 for p in root.rglob("*") if p.is_file())
                    return 0, f"{count}\n", ""
            except (ValueError, IndexError):
                pass
            return 0, "0\n", ""
        if "tail -n" in command:
            path = command.split()[-1]
            return 0, self.files.get(path, ""), ""
        return 0, "", ""

    def expand_remote_path(self, remote_path: str) -> str:
        """Fake path expansion; treat '~' as /home/{user}."""
        if remote_path.startswith("~"):
            home = f"/home/{self.user}"
            remote_path = home + remote_path[1:]
        return remote_path

    def sftp_put(self, local_path: str, remote_path: str) -> None:
        self.commands.append(f"sftp_put({local_path}, {remote_path})")
        self.files[remote_path] = Path(local_path).read_text(encoding="utf-8", errors="replace")

    def sftp_get(self, remote_path: str, local_path: str) -> None:
        self.commands.append(f"sftp_get({remote_path}, {local_path})")
        content = self.files.get(remote_path, "")
        Path(local_path).write_text(content, encoding="utf-8")

    def close(self) -> None:
        self.connected = False

    def set_response(self, pattern: str, rc: int, out: str = "", err: str = "") -> None:
        self.responses[pattern] = (rc, out, err)

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *args):
        self.close()


@pytest.fixture
def tmp_config(tmp_path: Path) -> Config:
    """A config that writes logs into tmp_path."""
    return Config(
        host="access-iris.uni.lu",
        port=8022,
        user="testuser",
        remote_project_dir=str(tmp_path / "remote" / "{project_name}"),
        default_partition="batch",
        default_nodes=1,
        default_ntasks=1,
        default_cpus_per_task=1,
        default_mem="4G",
        default_time="01:00:00",
        conda_module="miniconda3",
        python_module="lang/Python/3.11",
        sync_excludes=[".git", "__pycache__"],
        poll_interval=0,
        pending_timeout=3600,
        log_dir=str(tmp_path / "logs"),
    )


@pytest.fixture
def fake_ssh() -> FakeSSHClient:
    return FakeSSHClient()


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    """A small sample project."""
    p = tmp_path / "sample_project"
    p.mkdir()
    (p / "main.py").write_text("print('hello iris')\n", encoding="utf-8")
    (p / "requirements.txt").write_text("numpy\n", encoding="utf-8")
    return p


@pytest.fixture
def rsync_success(project_dir: Path) -> Callable[..., subprocess.CompletedProcess]:
    """Fake rsync that copies project_dir contents to a sibling remote dir."""

    def run(cmd, **kwargs):
        # cmd: rsync ... local_dir/ user@host:remote_dir/
        src = cmd[-2]
        dst_spec = cmd[-1]
        # dst_spec looks like user@host:/path/
        dst = dst_spec.split(":", 1)[1]
        dst_path = Path(dst)
        dst_path.mkdir(parents=True, exist_ok=True)
        for item in Path(src).iterdir():
            if item.is_file():
                shutil.copy2(item, dst_path / item.name)
            elif item.is_dir():
                shutil.copytree(item, dst_path / item.name, dirs_exist_ok=True)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    return run


@pytest.fixture
def rsync_fail_network() -> Callable[..., subprocess.CompletedProcess]:
    def run(cmd, **kwargs):
        return subprocess.CompletedProcess(
            cmd, 23, stdout="", stderr="rsync: connection unexpectedly closed\n"
        )

    return run
