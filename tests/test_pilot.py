"""Pilot project integration tests covering normal and failure flows."""

import shutil
import subprocess
from pathlib import Path

import pytest

from ulhpc_submit.config import Config
from ulhpc_submit.main import submit_hpc_task

import ulhpc_submit.main as main_module
import ulhpc_submit.sync as sync_module

from tests.conftest import FakeSSHClient


def _fake_rsync_success(cmd, **kwargs):
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


def _make_config(tmp_path: Path, remote: Path) -> Config:
    return Config(
        host="access-iris.uni.lu",
        port=8022,
        user="testuser",
        remote_project_dir=str(remote),
        default_partition="batch",
        default_nodes=1,
        default_ntasks=1,
        default_cpus_per_task=1,
        default_mem="4G",
        default_time="01:00:00",
        conda_module="miniconda3",
        python_module="lang/Python/3.11",
        sync_excludes=[".git", "__pycache__", ".ulhpc_submit", "Dockerfile"],
        poll_interval=0,
        pending_timeout=3600,
        log_dir=str(tmp_path / "logs"),
    )


def _read_logs(log_dir: Path) -> str:
    text = ""
    for run_dir in log_dir.glob("*"):
        log_file = run_dir / "run.log"
        if log_file.exists():
            text += log_file.read_text(encoding="utf-8", errors="ignore")
    return text


@pytest.fixture(autouse=True)
def _patch_and_restore(monkeypatch):
    original_ssh = main_module.SSHClient
    original_rsync = sync_module.subprocess.run
    yield
    main_module.SSHClient = original_ssh
    sync_module.subprocess.run = original_rsync


def test_pilot_success(tmp_path: Path):
    """Standard pilot run completes normally."""
    src_project = Path(__file__).resolve().parents[1] / "examples" / "pilot_project"
    project = tmp_path / "pilot_project"
    shutil.copytree(src_project, project, dirs_exist_ok=True)
    remote = tmp_path / "remote"
    remote.mkdir()

    class SuccessSSH(FakeSSHClient):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.set_response("find", 0, "4\n", "")
            self.set_response("sbatch", 0, "Submitted batch job 77777\n", "")
            self.set_response("squeue", 0, "", "")
            self.set_response("sacct", 0, "77777|COMPLETED|0:0|0:0|1M|node01\n", "")
            self.files[str(remote / "job_77777.out")] = (
                "-- PILOT RUN BEGIN --\n"
                "Read 2 lines from data.txt\n"
                "Numpy is installed and version: 1.26.0\n"
                "Simulating step 1/3 ...\n"
                "Simulating step 2/3 ...\n"
                "Simulating step 3/3 ...\n"
                "-- PILOT RUN DONE --\n"
            )
            self.files[str(remote / "job_77777.err")] = ""

    monkeypatch = pytest.MonkeyPatch()
    main_module.SSHClient = SuccessSSH
    sync_module.subprocess.run = _fake_rsync_success
    try:
        cfg = _make_config(tmp_path, remote)
        rc = submit_hpc_task(
            config=cfg,
            command=["python", "main.py"],
            local_dir=str(project),
            remote_dir=str(remote),
        )
        logs = _read_logs(tmp_path / "logs")
        assert rc == 0
        assert "PILOT RUN DONE" in logs
        assert "Submitted job 77777" in logs
    finally:
        monkeypatch.undo()


def test_pilot_dependency_missing(tmp_path: Path):
    """Pilot run with a missing package is classified as ENV_DEPENDENCY_ERROR."""
    project = tmp_path / "pilot_project"
    project.mkdir()
    (project / "main.py").write_text(
        "import nonexistent_pilot_pkg\nprint('never reached')\n", encoding="utf-8"
    )
    (project / "requirements.txt").write_text("numpy\n", encoding="utf-8")
    remote = tmp_path / "remote"
    remote.mkdir()

    class DepErrorSSH(FakeSSHClient):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.set_response("find", 0, "2\n", "")
            self.set_response("sbatch", 0, "Submitted batch job 88888\n", "")
            self.set_response("squeue", 0, "", "")
            self.set_response("sacct", 0, "88888|COMPLETED|0:0|0:0|1M|node01\n", "")
            self.files[str(remote / "job_88888.out")] = ""
            self.files[str(remote / "job_88888.err")] = (
                "ModuleNotFoundError: No module named 'nonexistent_pilot_pkg'\n"
            )

    monkeypatch = pytest.MonkeyPatch()
    main_module.SSHClient = DepErrorSSH
    sync_module.subprocess.run = _fake_rsync_success
    try:
        cfg = _make_config(tmp_path, remote)
        rc = submit_hpc_task(
            config=cfg,
            command=["python", "main.py"],
            local_dir=str(project),
            remote_dir=str(remote),
        )
        logs = _read_logs(tmp_path / "logs")
        assert rc == 1
        assert "ENV_DEPENDENCY_ERROR" in logs
    finally:
        monkeypatch.undo()


def test_pilot_code_error(tmp_path: Path):
    """Pilot run with a RuntimeError is classified as CODE_ERROR."""
    project = tmp_path / "pilot_project"
    project.mkdir()
    (project / "main.py").write_text(
        "raise RuntimeError('Pilot code error: this is a test.')\n",
        encoding="utf-8",
    )
    (project / "requirements.txt").write_text("numpy\n", encoding="utf-8")
    remote = tmp_path / "remote"
    remote.mkdir()

    class CodeErrorSSH(FakeSSHClient):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.set_response("find", 0, "2\n", "")
            self.set_response("sbatch", 0, "Submitted batch job 99999\n", "")
            self.set_response("squeue", 0, "", "")
            self.set_response("sacct", 0, "99999|COMPLETED|0:0|0:0|1M|node01\n", "")
            self.files[str(remote / "job_99999.out")] = (
                "Traceback (most recent call last):\n"
                "  File main.py, line 1, in <module>\n"
                "RuntimeError: Pilot code error: this is a test.\n"
            )
            self.files[str(remote / "job_99999.err")] = ""

    monkeypatch = pytest.MonkeyPatch()
    main_module.SSHClient = CodeErrorSSH
    sync_module.subprocess.run = _fake_rsync_success
    try:
        cfg = _make_config(tmp_path, remote)
        rc = submit_hpc_task(
            config=cfg,
            command=["python", "main.py"],
            local_dir=str(project),
            remote_dir=str(remote),
        )
        logs = _read_logs(tmp_path / "logs")
        assert rc == 1
        assert "CODE_ERROR" in logs
        assert "Pilot code error" in logs
    finally:
        monkeypatch.undo()


def test_pilot_resource_error(tmp_path: Path):
    """Pilot run killed by OOM is classified as HPC_RESOURCE_ERROR."""
    src_project = Path(__file__).resolve().parents[1] / "examples" / "pilot_project"
    project = tmp_path / "pilot_project"
    shutil.copytree(src_project, project, dirs_exist_ok=True)
    remote = tmp_path / "remote"
    remote.mkdir()

    class OomSSH(FakeSSHClient):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.set_response("find", 0, "4\n", "")
            self.set_response("sbatch", 0, "Submitted batch job 11111\n", "")
            self.set_response("squeue", 0, "", "")
            self.set_response(
                "sacct", 0, "11111|OUT_OF_MEMORY|0:0|0:0|16G|node01\n", ""
            )

    monkeypatch = pytest.MonkeyPatch()
    main_module.SSHClient = OomSSH
    sync_module.subprocess.run = _fake_rsync_success
    try:
        cfg = _make_config(tmp_path, remote)
        rc = submit_hpc_task(
            config=cfg,
            command=["python", "main.py"],
            local_dir=str(project),
            remote_dir=str(remote),
        )
        logs = _read_logs(tmp_path / "logs")
        assert rc == 1
        assert "HPC_RESOURCE_ERROR" in logs
    finally:
        monkeypatch.undo()


def test_pilot_conda_environment_yml(tmp_path: Path):
    """Pilot run using environment.yml creates conda env named in the file."""
    src_project = Path(__file__).resolve().parents[1] / "examples" / "pilot_project"
    project = tmp_path / "pilot_project"
    shutil.copytree(src_project, project, dirs_exist_ok=True)
    # Remove requirements.txt so environment.yml is used.
    (project / "requirements.txt").unlink(missing_ok=True)
    remote = tmp_path / "remote"
    remote.mkdir()

    class CondaSSH(FakeSSHClient):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.set_response("find", 0, "3\n", "")
            self.set_response("sbatch", 0, "Submitted batch job 44444\n", "")
            self.set_response("squeue", 0, "", "")
            self.set_response("sacct", 0, "44444|COMPLETED|0:0|0:0|1M|node01\n", "")
            self.files[str(remote / "job_44444.out")] = (
                "-- PILOT RUN BEGIN --\n"
                "Read 2 lines from data.txt\n"
                "Numpy is installed and version: 1.26.0\n"
                "Pandas DataFrame:\n    test\n0     1\n1     2\n2     3\n"
                "-- PILOT RUN DONE --\n"
            )
            self.files[str(remote / "job_44444.err")] = ""

    monkeypatch = pytest.MonkeyPatch()
    main_module.SSHClient = CondaSSH
    sync_module.subprocess.run = _fake_rsync_success
    try:
        cfg = _make_config(tmp_path, remote)
        rc = submit_hpc_task(
            config=cfg,
            command=["python", "main.py"],
            local_dir=str(project),
            remote_dir=str(remote),
            conda_env="pilot_conda",
        )
        logs = _read_logs(tmp_path / "logs")
        script_path = project / ".ulhpc_submit" / "generated_job.sh"
        script = script_path.read_text(encoding="utf-8")
        assert rc == 0
        assert "PILOT RUN DONE" in logs
        assert "conda env create -f environment.yml" in script
        assert "conda activate pilot_conda" in script
        assert "pip install -r requirements.txt" not in script
    finally:
        monkeypatch.undo()


def test_pilot_container(tmp_path: Path):
    """Pilot run with --container generates an Apptainer job script."""
    src_project = Path(__file__).resolve().parents[1] / "examples" / "pilot_project"
    project = tmp_path / "pilot_project"
    shutil.copytree(src_project, project, dirs_exist_ok=True)
    remote = tmp_path / "remote"
    remote.mkdir()

    class ContainerSSH(FakeSSHClient):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.set_response("find", 0, "4\n", "")
            self.set_response("sbatch", 0, "Submitted batch job 55555\n", "")
            self.set_response("squeue", 0, "", "")
            self.set_response("sacct", 0, "55555|COMPLETED|0:0|0:0|1M|node01\n", "")
            self.files[str(remote / "job_55555.out")] = "-- PILOT RUN DONE --\n"
            self.files[str(remote / "job_55555.err")] = ""

    monkeypatch = pytest.MonkeyPatch()
    main_module.SSHClient = ContainerSSH
    sync_module.subprocess.run = _fake_rsync_success
    try:
        cfg = _make_config(tmp_path, remote)
        rc = submit_hpc_task(
            config=cfg,
            command=["python", "main.py"],
            local_dir=str(project),
            remote_dir=str(remote),
            container="pilot-hpc.sif",
        )
        logs = _read_logs(tmp_path / "logs")
        script_path = project / ".ulhpc_submit" / "generated_job.sh"
        script = script_path.read_text(encoding="utf-8")
        assert rc == 0
        assert "module load apptainer" in script
        assert "apptainer exec pilot-hpc.sif python main.py" in script
        assert "PILOT RUN DONE" in logs
    finally:
        monkeypatch.undo()


def test_pilot_container_user_apptainer_command(tmp_path: Path):
    """If the user command already starts with apptainer, do not double-wrap."""
    src_project = Path(__file__).resolve().parents[1] / "examples" / "pilot_project"
    project = tmp_path / "pilot_project"
    shutil.copytree(src_project, project, dirs_exist_ok=True)
    remote = tmp_path / "remote"
    remote.mkdir()

    monkeypatch = pytest.MonkeyPatch()
    main_module.SSHClient = FakeSSHClient
    sync_module.subprocess.run = _fake_rsync_success
    try:
        cfg = _make_config(tmp_path, remote)
        rc = submit_hpc_task(
            config=cfg,
            command=["apptainer", "run", "pilot-hpc.sif"],
            local_dir=str(project),
            remote_dir=str(remote),
            container="pilot-hpc.sif",
            dry_run=True,
        )
        script_path = project / ".ulhpc_submit" / "generated_job.sh"
        script = script_path.read_text(encoding="utf-8")
        assert rc == 0
        assert "module load apptainer" in script
        assert "apptainer run pilot-hpc.sif" in script
        assert "apptainer exec" not in script
    finally:
        monkeypatch.undo()
