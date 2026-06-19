"""Main orchestration: sync, env, script, submit, monitor, logs."""

import sys
import traceback
from pathlib import Path
from typing import Callable, List, Optional

from .config import Config
from .env_manager import EnvironmentManager
from .errors import ULHPCError
from .job_manager import JobManager
from .job_script import JobScriptBuilder
from .logs import LogManager, RunLogger, create_run_logger
from .monitor import JobMonitor
from .ssh_client import SSHClient
from .sync import CodeSync


class SubmissionPipeline:
    """End-to-end pipeline for a single HPC task submission."""

    def __init__(
        self,
        config: Config,
        command: List[str],
        local_dir: str,
        remote_dir: Optional[str] = None,
        job_name: Optional[str] = None,
        partition: Optional[str] = None,
        nodes: Optional[int] = None,
        ntasks: Optional[int] = None,
        cpus: Optional[int] = None,
        mem: Optional[str] = None,
        time: Optional[str] = None,
        gpus: Optional[int] = None,
        conda_env: Optional[str] = None,
        container: Optional[str] = None,
        no_sync: bool = False,
        full_logs: bool = False,
        dry_run: bool = False,
        progress: Optional[Callable[[str], None]] = None,
    ):
        self.config = config
        self.command = command
        self.local_dir = Path(local_dir).resolve()
        self.remote_dir = remote_dir or config.expand_remote_project_dir(self.local_dir.name)
        self.job_name = job_name or self.local_dir.name
        self.partition = partition
        self.nodes = nodes
        self.ntasks = ntasks
        self.cpus = cpus
        self.mem = mem
        self.time = time
        self.gpus = gpus
        self.conda_env = conda_env
        self.container = container
        self.no_sync = no_sync
        self.full_logs = full_logs
        self.dry_run = dry_run
        self.progress = progress or print

        self.ssh: Optional[SSHClient] = None
        self.logger: Optional[RunLogger] = None
        self.job_id: Optional[str] = None

    def _announce(self, message: str) -> None:
        self.progress(f"[ulhpc-submit] {message}")
        if self.logger:
            self.logger.info(message)

    def _open_ssh(self) -> SSHClient:
        ssh = SSHClient(
            host=self.config.host,
            port=self.config.port,
            user=self.config.user,
            key_path=self.config.ssh_key,
            key_passphrase=self.config.ssh_key_passphrase,
            max_retries=self.config.max_ssh_retries,
        )
        ssh.connect()
        return ssh

    def run(self) -> int:
        """Execute the full pipeline and return an exit code."""
        self.logger = create_run_logger(
            self.config.resolved_log_dir(), self.local_dir.name
        )
        self._announce(f"Starting run; local log: {self.logger.log_file}")

        try:
            self.ssh = self._open_ssh()
            self._announce(
                f"Connected to {self.config.user}@{self.config.host}:{self.config.port}"
            )
            # Resolve ~ and relative paths to absolute remote path for SFTP.
            self.remote_dir = self.ssh.expand_remote_path(self.remote_dir)

            # 1. Sync code
            if not self.no_sync:
                self._announce(f"Syncing {self.local_dir} -> {self.remote_dir}")
                sync = CodeSync(
                    ssh=self.ssh,
                    local_dir=str(self.local_dir),
                    remote_dir=self.remote_dir,
                    excludes=self.config.sync_excludes,
                )
                if self.dry_run:
                    self._announce(f"Dry-run rsync command: {sync.sync_dry_run()}")
                else:
                    sync.sync()
                    self._announce("Code sync complete")
            else:
                self._announce("Skipping code sync (--no-sync)")

            # 2. Validate dependency files
            env_manager = EnvironmentManager(
                str(self.local_dir),
                conda_module=self.config.conda_module,
                python_module=self.config.python_module,
                conda_env=self.conda_env,
            )
            env_manager.validate_local_dependencies()
            deps = env_manager.detect_dependency_files()
            self._announce(
                f"Detected dependencies: requirements.txt={deps['requirements']}, "
                f"environment.yml={deps['environment_yml']}"
            )

            # 3. Build job script
            builder = JobScriptBuilder(
                config=self.config,
                command=self.command,
                project_dir=str(self.local_dir),
                remote_dir=self.remote_dir,
                env_manager=env_manager,
                job_name=self.job_name,
                partition=self.partition,
                nodes=self.nodes,
                ntasks=self.ntasks,
                cpus_per_task=self.cpus,
                mem=self.mem,
                time=self.time,
                gpus=self.gpus,
                conda_env=self.conda_env,
                container=self.container,
            )
            script_path = builder.write()
            self._announce(f"Generated Slurm script: {script_path}")
            if self.dry_run:
                self._announce("Dry-run mode: printing generated script")
                self.progress(builder.build())
                self._announce("Dry-run complete; no job submitted.")
                return 0

            # 4. Submit job
            job_manager = JobManager(self.ssh, self.remote_dir)
            remote_script = job_manager.upload_script(script_path)
            self.job_id = job_manager.submit(remote_script)
            self._announce(f"Submitted job {self.job_id}")
            self.logger.event("JOB", "SUBMITTED", f"job_id={self.job_id}")

            # 5. Monitor
            monitor = JobMonitor(
                ssh=self.ssh,
                job_id=self.job_id,
                poll_interval=self.config.poll_interval,
                pending_timeout=self.config.pending_timeout,
                progress=self.progress,
            )
            self._announce("Monitoring job state...")
            final_event = monitor.monitor()
            self._announce(f"Job {self.job_id} finished with state {final_event.state}")
            self.logger.event(
                "JOB", final_event.state, final_event.info
            )

            # 6. Fetch logs
            log_manager = LogManager(
                ssh=self.ssh,
                remote_dir=self.remote_dir,
                job_id=self.job_id,
                run_logger=self.logger,
                full_logs=self.full_logs,
            )
            self._announce("Fetching remote output...")
            stdout, stderr = log_manager.fetch()
            log_manager.merge(stdout, stderr)

            classified = log_manager.classify_output_errors(stdout, stderr)
            if classified:
                self._announce(f"Detected issue: {classified.code}")
                self.logger.error(str(classified))
                # Surface the structured error to the user.
                self.progress(str(classified))
                return 1

            self._announce(f"Run complete. Log: {self.logger.log_file}")
            return 0

        except ULHPCError as exc:
            self._announce(f"Pipeline failed: {exc.code}")
            if self.logger:
                self.logger.error(str(exc))
            self.progress(str(exc))
            return 1
        except Exception as exc:  # noqa: BLE001
            msg = f"Unexpected error: {exc}\n{traceback.format_exc()}"
            self._announce("Pipeline failed with unclassified error")
            if self.logger:
                self.logger.error(msg)
            self.progress(msg)
            return 2
        finally:
            if self.ssh:
                self.ssh.close()


def submit_hpc_task(
    config: Config,
    command: List[str],
    local_dir: str = ".",
    **kwargs,
) -> int:
    """High-level helper to run the pipeline."""
    pipeline = SubmissionPipeline(config=config, command=command, local_dir=local_dir, **kwargs)
    return pipeline.run()
