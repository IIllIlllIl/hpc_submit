"""End-to-end integration test with a mocked SSH client."""

from pathlib import Path

from ulhpc_submit.config import Config
from ulhpc_submit.main import submit_hpc_task


def test_full_pipeline_success(project_dir: Path, tmp_path: Path):
    """A complete successful submission flow using a fake SSH client."""
    from tests.conftest import FakeSSHClient

    class IntegrationSSH(FakeSSHClient):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.remote_root = tmp_path / "remote" / "sample_project"
            self.remote_root.mkdir(parents=True, exist_ok=True)
            self.set_response("sbatch", 0, "Submitted batch job 99999\n", "")
            self.set_response("squeue", 0, "", "")  # job immediately gone
            self.set_response(
                "sacct", 0, "99999|COMPLETED|0:0|0:0|1M|node01\n", ""
            )

        def sftp_put(self, local_path: str, remote_path: str) -> None:
            super().sftp_put(local_path, remote_path)
            # Mirror the script to local remote root for inspection.
            rel = Path(remote_path).name
            dest = self.remote_root / rel
            dest.write_text(Path(local_path).read_text(encoding="utf-8"), encoding="utf-8")

        def exec_command(self, command: str):
            self.commands.append(command)
            for pattern, (rc, out, err) in self.responses.items():
                if pattern in command:
                    return rc, out, err
            if "test -d" in command and "-w" in command:
                return 0, "EXISTS\nWRITABLE\n", ""
            if "find " in command and "wc -l" in command:
                # Count files that rsync_success copied.
                count = sum(1 for _ in self.remote_root.rglob("*") if _.is_file())
                return 0, f"{count}\n", ""
            if "tail -n" in command:
                path = command.split()[-2]
                content = self.files.get(path, "")
                return 0, content, ""
            return 0, "", ""

    import ulhpc_submit.main as main_module
    import ulhpc_submit.sync as sync_module
    import ulhpc_submit.job_manager as jm_module
    import ulhpc_submit.monitor as mon_module
    import ulhpc_submit.logs as logs_module

    monkeypatch = None  # we can't use fixture here; set class refs manually
    # Use direct monkeypatching of the class references used to instantiate.
    original_ssh = main_module.SSHClient
    main_module.SSHClient = IntegrationSSH

    # Need to simulate rsync success that copies files to remote_root.
    import shutil
    import subprocess

    remote_root = tmp_path / "remote" / "sample_project"

    def fake_rsync(cmd, **kwargs):
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

    original_rsync = sync_module.subprocess.run
    sync_module.subprocess.run = fake_rsync

    cfg = Config(
        host="access-iris.uni.lu",
        port=8022,
        user="testuser",
        remote_project_dir=str(remote_root),
        default_partition="batch",
        default_nodes=1,
        default_ntasks=1,
        default_cpus_per_task=1,
        default_mem="4G",
        default_time="01:00:00",
        conda_module="miniconda3",
        python_module="lang/Python/3.11",
        sync_excludes=[".git"],
        poll_interval=0,
        pending_timeout=3600,
        log_dir=str(tmp_path / "logs"),
    )

    try:
        rc = submit_hpc_task(
            config=cfg,
            command=["python", "main.py"],
            local_dir=str(project_dir),
            remote_dir=str(remote_root),
        )
        assert rc == 0

        # Verify generated script.
        script_path = project_dir / ".ulhpc_submit" / "generated_job.sh"
        assert script_path.exists()
        script = script_path.read_text(encoding="utf-8")
        assert "module load miniconda3" in script
        assert "python main.py" in script
        assert "#SBATCH --partition=batch" in script
    finally:
        main_module.SSHClient = original_ssh
        sync_module.subprocess.run = original_rsync
