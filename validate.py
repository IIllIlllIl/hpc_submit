#!/usr/bin/env python3
"""Automated acceptance test runner for ulhpc-submit.

This script exercises both the pytest suite and a set of end-to-end scenarios
with mocked SSH/Slurm infrastructure. It writes a Markdown report and prints
a summary.
"""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

# Import module under test
from ulhpc_submit.config import Config
from ulhpc_submit.main import submit_hpc_task

import ulhpc_submit.main as main_module
import ulhpc_submit.monitor as monitor_module
import ulhpc_submit.sync as sync_module


class FakeSSHClient:
    """Configurable SSH client for acceptance scenarios."""

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
        self.key_passphrase = key_passphrase
        self.connected = False
        self.commands: List[str] = []
        self.responses: Dict[str, Tuple[int, str, str]] = {}
        self.files: Dict[str, str] = {}

    def connect(self):
        self.connected = True

    def exec_command(self, command: str) -> Tuple[int, str, str]:
        self.commands.append(command)
        for pattern, (rc, out, err) in self.responses.items():
            if pattern in command:
                return rc, out, err
        if "test -d" in command and "-w" in command:
            return 0, "EXISTS\nWRITABLE\n", ""
        if "mkdir -p" in command:
            return 0, "", ""
        if "df -h" in command:
            return 0, "Filesystem Size Used Avail Use% Mounted\n/dev/sda 100G 10G 90G 10% /", ""
        if "find " in command and "wc -l" in command:
            return 0, "1\n", ""
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
        Path(local_path).write_text(self.files.get(remote_path, ""), encoding="utf-8")

    def close(self) -> None:
        self.connected = False

    def set_response(self, pattern: str, rc: int, out: str = "", err: str = "") -> None:
        self.responses[pattern] = (rc, out, err)


def make_config(log_dir: Path, remote_dir: Path) -> Config:
    return Config(
        host="access-iris.uni.lu",
        port=8022,
        user="testuser",
        remote_project_dir=str(remote_dir),
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
        log_dir=str(log_dir),
    )


def read_logs(log_dir: Path) -> str:
    text = ""
    for run_dir in log_dir.glob("*"):
        log_file = run_dir / "run.log"
        if log_file.exists():
            text += log_file.read_text(encoding="utf-8", errors="ignore")
    return text


def fake_rsync_success(cmd: List[str], **kwargs) -> subprocess.CompletedProcess:
    src = cmd[-2]
    dst_spec = cmd[-1]
    dst = Path(dst_spec.split(":", 1)[1])
    dst.mkdir(parents=True, exist_ok=True)
    for item in Path(src).iterdir():
        if item.is_file():
            shutil.copy2(item, dst / item.name)
        elif item.is_dir():
            shutil.copytree(item, dst / item.name, dirs_exist_ok=True)
    return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")


def fake_rsync_network_fail(cmd: List[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        cmd, 23, stdout="", stderr="rsync: connection unexpectedly closed"
    )


def fake_rsync_disk_full(cmd: List[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        cmd, 23, stdout="", stderr="rsync: write failed on ... (No space left on device)"
    )


def run_scenario(name: str, description: str, runner: Callable[[], bool]) -> Tuple[bool, str]:
    print(f"Running scenario: {name} ...", end=" ")
    try:
        ok = runner()
        status = "PASS" if ok else "FAIL"
    except Exception as exc:
        status = "FAIL"
        description += f"\n\nException: {exc}"
    print(status)
    return status == "PASS", description


def main() -> int:
    repo_root = Path(__file__).resolve().parent
    report_lines: List[str] = [
        "# UL HPC Submit Validation Report\n",
        f"Date: {__import__('datetime').datetime.now().isoformat()}\n",
    ]

    # 1. Run pytest suite
    print("Running pytest suite...")
    pytest_result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-v", "--tb=short"],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    pytest_pass = pytest_result.returncode == 0
    report_lines.append("## Pytest Suite\n")
    report_lines.append(f"- Result: {'PASS' if pytest_pass else 'FAIL'}\n")
    report_lines.append(f"- Exit code: {pytest_result.returncode}\n")
    report_lines.append("<details><summary>Pytest output</summary>\n\n```\n")
    report_lines.append(pytest_result.stdout[-4000:] + pytest_result.stderr[-2000:])
    report_lines.append("\n```\n</details>\n\n")

    # Save original classes/functions
    original_ssh_client = main_module.SSHClient
    original_subprocess_run = sync_module.subprocess.run
    original_time = monitor_module.time.time

    scenarios: List[Tuple[str, str, Callable[[], bool]]] = []

    # Scenario: dry-run success
    def dry_run_success() -> bool:
        main_module.SSHClient = FakeSSHClient
        sync_module.subprocess.run = fake_rsync_success
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = tmp_path / "proj"
            project.mkdir()
            (project / "main.py").write_text("print('hi')\n", encoding="utf-8")
            (project / "requirements.txt").write_text("numpy\n", encoding="utf-8")
            cfg = make_config(tmp_path / "logs", tmp_path / "remote")
            rc = submit_hpc_task(
                config=cfg,
                command=["python", "main.py"],
                local_dir=str(project),
                dry_run=True,
            )
            script_path = project / ".ulhpc_submit" / "generated_job.sh"
            if not script_path.exists():
                return False
            script = script_path.read_text(encoding="utf-8")
            return rc == 0 and "module load miniconda3" in script and "python main.py" in script

    scenarios.append(("dry-run-success", "Generate a valid Slurm script without submitting.", dry_run_success))

    # Scenario: sync network failure
    def sync_network_failure() -> bool:
        main_module.SSHClient = FakeSSHClient
        sync_module.subprocess.run = fake_rsync_network_fail
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = tmp_path / "proj"
            project.mkdir()
            (project / "main.py").write_text("print('hi')\n", encoding="utf-8")
            cfg = make_config(tmp_path / "logs", tmp_path / "remote")
            rc = submit_hpc_task(
                config=cfg,
                command=["python", "main.py"],
                local_dir=str(project),
            )
            return rc == 1 and "SYNC_NETWORK_ERROR" in read_logs(tmp_path / "logs")

    scenarios.append(("sync-network-failure", "Detect and report rsync network errors.", sync_network_failure))

    # Scenario: sync disk full
    def sync_disk_full() -> bool:
        main_module.SSHClient = FakeSSHClient
        sync_module.subprocess.run = fake_rsync_disk_full
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = tmp_path / "proj"
            project.mkdir()
            (project / "main.py").write_text("print('hi')\n", encoding="utf-8")
            cfg = make_config(tmp_path / "logs", tmp_path / "remote")
            rc = submit_hpc_task(
                config=cfg,
                command=["python", "main.py"],
                local_dir=str(project),
            )
            return rc == 1 and "SYNC_DISK_FULL" in read_logs(tmp_path / "logs")

    scenarios.append(("sync-disk-full", "Detect and report remote disk-full errors.", sync_disk_full))

    # Scenario: invalid resources
    def invalid_resources() -> bool:
        class BadSSH(FakeSSHClient):
            def __init__(self, **kwargs):
                super().__init__(**kwargs)
                self.set_response("find", 0, "1\n", "")
                self.set_response(
                    "sbatch",
                    1,
                    "",
                    "sbatch: error: Batch job submission failed: Requested node configuration is not available",
                )

        main_module.SSHClient = BadSSH
        sync_module.subprocess.run = fake_rsync_success
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = tmp_path / "proj"
            project.mkdir()
            (project / "main.py").write_text("print('hi')\n", encoding="utf-8")
            cfg = make_config(tmp_path / "logs", tmp_path / "remote")
            rc = submit_hpc_task(
                config=cfg,
                command=["python", "main.py"],
                local_dir=str(project),
            )
            return rc == 1 and "JOB_INVALID_RESOURCES" in read_logs(tmp_path / "logs")

    scenarios.append(("job-invalid-resources", "Detect sbatch resource errors.", invalid_resources))

    # Scenario: pending timeout
    def pending_timeout() -> bool:
        class FastClock:
            def __init__(self):
                self.t = 0

            def __call__(self):
                self.t += 20
                return self.t

        class PendingSSH(FakeSSHClient):
            def __init__(self, **kwargs):
                super().__init__(**kwargs)
                self.set_response("find", 0, "1\n", "")
                self.set_response("sbatch", 0, "Submitted batch job 55555\n", "")
                self.set_response("squeue", 0, 'PENDING 00:00:00 01:00:00 Resources\n', "")

        main_module.SSHClient = PendingSSH
        sync_module.subprocess.run = fake_rsync_success
        monitor_module.time.time = FastClock()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                tmp_path = Path(tmp)
                project = tmp_path / "proj"
                project.mkdir()
                (project / "main.py").write_text("print('hi')\n", encoding="utf-8")
                cfg = make_config(tmp_path / "logs", tmp_path / "remote")
                cfg.pending_timeout = 10
                rc = submit_hpc_task(
                    config=cfg,
                    command=["python", "main.py"],
                    local_dir=str(project),
                )
                return rc == 1 and "JOB_PENDING_TIMEOUT" in read_logs(tmp_path / "logs")
        finally:
            monitor_module.time.time = original_time

    scenarios.append(("job-pending-timeout", "Detect excessive queue wait time.", pending_timeout))

    # Scenario: code traceback
    def code_traceback() -> bool:
        class TracebackSSH(FakeSSHClient):
            def __init__(self, **kwargs):
                super().__init__(**kwargs)
                self.set_response("find", 0, "1\n", "")
                self.set_response("sbatch", 0, "Submitted batch job 11111\n", "")
                self.set_response("squeue", 0, "", "")
                self.set_response("sacct", 0, "11111|COMPLETED|0:0|0:0|1M|node01\n", "")

        main_module.SSHClient = TracebackSSH
        sync_module.subprocess.run = fake_rsync_success
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = tmp_path / "proj"
            remote = tmp_path / "remote"
            project.mkdir()
            remote.mkdir()
            (project / "main.py").write_text("raise ValueError('demo')\n", encoding="utf-8")
            cfg = make_config(tmp_path / "logs", remote)
            ssh = TracebackSSH()
            ssh.files[f"{remote}/job_11111.out"] = "Traceback (most recent call last):\nValueError: demo\n"
            ssh.files[f"{remote}/job_11111.err"] = ""
            main_module.SSHClient = lambda **kw: ssh
            rc = submit_hpc_task(
                config=cfg,
                command=["python", "main.py"],
                local_dir=str(project),
                remote_dir=str(remote),
            )
            return rc == 1 and "CODE_ERROR" in read_logs(tmp_path / "logs")

    scenarios.append(("code-traceback", "Classify Python traceback as CODE_ERROR.", code_traceback))

    # Scenario: missing package
    def missing_package() -> bool:
        class MissingPkgSSH(FakeSSHClient):
            def __init__(self, **kwargs):
                super().__init__(**kwargs)
                self.set_response("find", 0, "1\n", "")
                self.set_response("sbatch", 0, "Submitted batch job 22222\n", "")
                self.set_response("squeue", 0, "", "")
                self.set_response("sacct", 0, "22222|COMPLETED|0:0|0:0|1M|node01\n", "")

        main_module.SSHClient = MissingPkgSSH
        sync_module.subprocess.run = fake_rsync_success
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = tmp_path / "proj"
            remote = tmp_path / "remote"
            project.mkdir()
            remote.mkdir()
            (project / "main.py").write_text("import torch\n", encoding="utf-8")
            cfg = make_config(tmp_path / "logs", remote)
            ssh = MissingPkgSSH()
            ssh.files[f"{remote}/job_22222.out"] = ""
            ssh.files[f"{remote}/job_22222.err"] = "ModuleNotFoundError: No module named 'torch'\n"
            main_module.SSHClient = lambda **kw: ssh
            rc = submit_hpc_task(
                config=cfg,
                command=["python", "main.py"],
                local_dir=str(project),
                remote_dir=str(remote),
            )
            return rc == 1 and "ENV_DEPENDENCY_ERROR" in read_logs(tmp_path / "logs")

    scenarios.append(("missing-package", "Classify missing package as ENV_DEPENDENCY_ERROR.", missing_package))

    # Scenario: OOM kill
    def oom_kill() -> bool:
        class OomSSH(FakeSSHClient):
            def __init__(self, **kwargs):
                super().__init__(**kwargs)
                self.set_response("find", 0, "1\n", "")
                self.set_response("sbatch", 0, "Submitted batch job 33333\n", "")
                self.set_response("squeue", 0, "", "")
                self.set_response("sacct", 0, "33333|OUT_OF_MEMORY|0:0|0:0|16G|node01\n", "")

        main_module.SSHClient = OomSSH
        sync_module.subprocess.run = fake_rsync_success
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = tmp_path / "proj"
            project.mkdir()
            (project / "main.py").write_text("print('hi')\n", encoding="utf-8")
            cfg = make_config(tmp_path / "logs", tmp_path / "remote")
            rc = submit_hpc_task(
                config=cfg,
                command=["python", "main.py"],
                local_dir=str(project),
            )
            return rc == 1 and "HPC_RESOURCE_ERROR" in read_logs(tmp_path / "logs")

    scenarios.append(("oom-kill", "Classify OUT_OF_MEMORY as HPC_RESOURCE_ERROR.", oom_kill))

    results: List[Tuple[str, str, bool]] = []
    report_lines.append("## Scenarios\n")
    report_lines.append("| Scenario | Description | Result |\n")
    report_lines.append("|---|---|---|\n")

    for name, desc, runner in scenarios:
        ok, full_desc = run_scenario(name, desc, runner)
        results.append((name, full_desc, ok))
        report_lines.append(f"| {name} | {full_desc.replace(chr(10), ' ')} | {'PASS' if ok else 'FAIL'} |\n")

    # Restore originals
    main_module.SSHClient = original_ssh_client
    sync_module.subprocess.run = original_subprocess_run
    monitor_module.time.time = original_time

    all_pass = pytest_pass and all(ok for _, _, ok in results)
    report_lines.append("\n## Summary\n")
    report_lines.append(f"- Overall: {'PASS' if all_pass else 'FAIL'}\n")
    report_lines.append(f"- Pytest: {'PASS' if pytest_pass else 'FAIL'}\n")
    report_lines.append(f"- Scenarios passed: {sum(1 for _, _, ok in results if ok)}/{len(results)}\n")

    report_path = repo_root / "validation_report.md"
    report_path.write_text("".join(report_lines), encoding="utf-8")
    print(f"\nReport written to {report_path}")
    return 0 if all_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
