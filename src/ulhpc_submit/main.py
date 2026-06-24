"""Main orchestration: sync, env, script, submit, monitor, logs."""

import sys
import traceback
import shlex
import yaml
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .config import Config
from .env_manager import EnvironmentManager
from .errors import ConfigError, StagingError, ULHPCError
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
        runtime_modules: Optional[List[str]] = None,
        python_executable: Optional[str] = None,
        use_conda: Optional[bool] = None,
        container: Optional[str] = None,
        apptainer_cache_dir: Optional[str] = None,
        apptainer_tmp_dir: Optional[str] = None,
        apptainer_sif_cache_dir: Optional[str] = None,
        no_sync: bool = False,
        stage_data: Optional[List[str]] = None,
        link_as: Optional[List[str]] = None,
        data_mounts: Optional[List[Dict[str, str]]] = None,
        persistent_output: Optional[List[str]] = None,
        persistent_outputs: Optional[List[Dict[str, str]]] = None,
        sync_remote_extra_policy: Optional[str] = None,
        full_logs: bool = False,
        submit_only: bool = False,
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
        self.runtime_modules = runtime_modules if runtime_modules is not None else config.runtime_modules
        self.python_executable = python_executable or config.python_executable
        self.use_conda = config.use_conda if use_conda is None else use_conda
        self.container = container
        self.apptainer_cache_dir = apptainer_cache_dir or config.apptainer_cache_dir
        self.apptainer_tmp_dir = apptainer_tmp_dir or config.apptainer_tmp_dir
        self.apptainer_sif_cache_dir = (
            apptainer_sif_cache_dir or config.apptainer_sif_cache_dir
        )
        self.no_sync = no_sync
        self.stage_data = stage_data or []
        self.link_as = link_as or []
        self.data_mounts = self._build_data_mounts(data_mounts)
        self.persistent_output = persistent_output or []
        self.persistent_outputs = self._build_persistent_outputs(persistent_outputs)
        self.sync_remote_extra_policy = (
            sync_remote_extra_policy or config.sync_remote_extra_policy
        )
        self.full_logs = full_logs
        self.submit_only = submit_only
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

    def _build_data_mounts(
        self, explicit_mounts: Optional[List[Dict[str, str]]]
    ) -> List[Dict[str, str]]:
        mounts = [dict(item) for item in self.config.data_mounts]
        if explicit_mounts:
            mounts.extend(dict(item) for item in explicit_mounts)
        if self.link_as and len(self.link_as) != len(self.stage_data):
            raise ConfigError(
                "--link-as must be provided once for each --stage-data entry."
            )
        for idx, spec in enumerate(self.stage_data):
            if ":" not in spec:
                raise ConfigError(
                    f"Invalid --stage-data value {spec!r}; expected LOCAL:REMOTE."
                )
            local, remote = spec.split(":", 1)
            mount = {"local": local, "remote": remote}
            if self.link_as:
                mount["link_as"] = self.link_as[idx]
            mounts.append(mount)
        return mounts

    def _build_persistent_outputs(
        self, explicit_outputs: Optional[List[Dict[str, str]]]
    ) -> List[Dict[str, str]]:
        outputs = [dict(item) for item in self.config.persistent_outputs]
        if explicit_outputs:
            outputs.extend(dict(item) for item in explicit_outputs)
        for spec in self.persistent_output:
            if ":" not in spec:
                raise ConfigError(
                    f"Invalid --persistent-output value {spec!r}; expected PROJECT_PATH:REMOTE."
                )
            project_path, remote = spec.split(":", 1)
            outputs.append({"project_path": project_path, "remote": remote})
        return outputs

    def _remote_stdout_path(self, job_id: str) -> str:
        return f"{self.remote_dir}/job_{job_id}.out"

    def _remote_stderr_path(self, job_id: str) -> str:
        return f"{self.remote_dir}/job_{job_id}.err"

    def _announce_submit_only_details(self, job_id: str) -> None:
        self._announce("Submit-only mode: job submitted; skipping monitoring and log fetch.")
        self._announce(f"Job ID: {job_id}")
        self._announce(f"Remote workdir: {self.remote_dir}")
        self._announce(f"Remote stdout: {self._remote_stdout_path(job_id)}")
        self._announce(f"Remote stderr: {self._remote_stderr_path(job_id)}")
        self._announce(f"Check queue: squeue -j {job_id}")
        self._announce(f"Check accounting: sacct -j {job_id}")

    def _remote_project_path(self, project_path: str) -> str:
        if project_path.startswith("/"):
            return project_path
        return f"{self.remote_dir}/{project_path.strip('/')}"

    def _create_remote_symlink(self, target: str, link_path: str) -> None:
        parent = str(Path(link_path).parent)
        cmd = (
            f"mkdir -p {shlex.quote(parent)} && "
            f"if [ -e {shlex.quote(link_path)} ] && [ ! -L {shlex.quote(link_path)} ]; then "
            f"echo 'link path exists and is not a symlink: {link_path}' >&2; exit 2; "
            f"fi && ln -sfn {shlex.quote(target)} {shlex.quote(link_path)}"
        )
        rc, _, err = self.ssh.exec_command(cmd)  # type: ignore[union-attr]
        if rc != 0:
            raise StagingError(err.strip() or f"Failed to link {link_path} -> {target}")

    def _stage_data_mounts(self) -> None:
        if not self.data_mounts:
            return
        for mount in self.data_mounts:
            local = mount.get("local")
            remote = mount.get("remote")
            link_as = mount.get("link_as")
            if not local or not remote:
                raise ConfigError("Each data mount requires local and remote paths.")
            local_path = Path(local).expanduser().resolve()
            if not local_path.is_dir():
                raise ConfigError(f"Staged data path must be a directory: {local_path}")
            remote_path = self.ssh.expand_remote_path(remote)  # type: ignore[union-attr]
            self._announce(f"Staging data {local_path} -> {remote_path}")
            sync = CodeSync(
                ssh=self.ssh,  # type: ignore[arg-type]
                local_dir=str(local_path),
                remote_dir=remote_path,
                excludes=[],
                progress=self.progress,
                free_space_margin=self.config.sync_free_space_margin,
            )
            sync.sync()
            if link_as:
                link_path = self._remote_project_path(link_as)
                self._create_remote_symlink(remote_path, link_path)
                self._announce(f"Linked staged data {link_path} -> {remote_path}")

    def _prepare_persistent_outputs(self) -> None:
        if not self.persistent_outputs:
            return
        for item in self.persistent_outputs:
            project_path = item.get("project_path") or item.get("local") or item.get("path")
            remote = item.get("remote") or item.get("remote_path")
            if not project_path or not remote:
                raise ConfigError(
                    "Each persistent output requires project_path and remote paths."
                )
            remote_path = self.ssh.expand_remote_path(remote)  # type: ignore[union-attr]
            rc, _, err = self.ssh.exec_command(  # type: ignore[union-attr]
                f"mkdir -p {shlex.quote(remote_path)}"
            )
            if rc != 0:
                raise StagingError(
                    err.strip() or f"Failed to create persistent output {remote_path}"
                )
            link_path = self._remote_project_path(project_path)
            self._create_remote_symlink(remote_path, link_path)
            self._announce(f"Linked persistent output {link_path} -> {remote_path}")

    def _safe_config_summary(self) -> Dict[str, object]:
        data = dict(self.config.__dict__)
        for key in ("ssh_key", "ssh_key_passphrase"):
            if data.get(key):
                data[key] = "<redacted>"
        data.update(
            {
                "remote_dir": self.remote_dir,
                "job_name": self.job_name,
                "command": self.command,
                "submit_only": self.submit_only,
                "no_sync": self.no_sync,
                "runtime_modules": self.runtime_modules,
                "python_executable": self.python_executable,
                "use_conda": self.use_conda,
                "container": self.container,
                "sync_remote_extra_policy": self.sync_remote_extra_policy,
            }
        )
        return data

    def _print_dry_run_plan(self, sync: Optional[CodeSync], script: str) -> None:
        self._announce("Dry-run plan")
        self.progress("=== final configuration ===")
        self.progress(yaml.safe_dump(self._safe_config_summary(), sort_keys=True).rstrip())
        self.progress("=== rsync plan ===")
        if self.no_sync:
            self.progress("code sync: skipped (--no-sync)")
        elif sync:
            self.progress(f"command: {sync.sync_dry_run()}")
            self.progress(f"excludes: {', '.join(self.config.sync_excludes) or '(none)'}")
            self.progress(
                f"estimated upload size: {CodeSync.format_upload_size(str(self.local_dir), self.config.sync_excludes)}"
            )
        self.progress("=== remote paths ===")
        self.progress(f"workdir: {self.remote_dir}")
        self.progress("stdout: job_%j.out")
        self.progress("stderr: job_%j.err")
        if self.data_mounts:
            self.progress("=== data staging ===")
            for mount in self.data_mounts:
                self.progress(
                    f"{mount.get('local')} -> {mount.get('remote')} link_as={mount.get('link_as', '')}"
                )
        if self.persistent_outputs:
            self.progress("=== persistent outputs ===")
            for item in self.persistent_outputs:
                project_path = item.get("project_path") or item.get("local") or item.get("path")
                remote = item.get("remote") or item.get("remote_path")
                self.progress(f"{project_path} -> {remote}")
        self.progress("=== slurm script ===")
        self.progress(script)

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
            sync = None
            if not self.no_sync:
                self._announce(f"Syncing {self.local_dir} -> {self.remote_dir}")
                sync = CodeSync(
                    ssh=self.ssh,
                    local_dir=str(self.local_dir),
                    remote_dir=self.remote_dir,
                    excludes=self.config.sync_excludes,
                    progress=self.progress,
                    free_space_margin=self.config.sync_free_space_margin,
                    remote_extra_policy=self.sync_remote_extra_policy,
                )
                if self.dry_run:
                    self._announce(f"Dry-run rsync command: {sync.sync_dry_run()}")
                else:
                    sync.sync()
                    self._announce("Code sync complete")
            else:
                self._announce("Skipping code sync (--no-sync)")

            if not self.dry_run:
                # 1b. Stage external data outside the project sync tree.
                self._stage_data_mounts()

                # 1c. Link persistent output/state directories into the project.
                self._prepare_persistent_outputs()

            # 2. Validate dependency files
            env_manager = EnvironmentManager(
                str(self.local_dir),
                conda_module=self.config.conda_module,
                python_module=self.config.python_module,
                conda_env=self.conda_env,
                runtime_modules=self.runtime_modules,
                python_executable=self.python_executable,
                use_conda=self.use_conda,
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
                runtime_modules=self.runtime_modules,
                python_executable=self.python_executable,
                use_conda=self.use_conda,
                container=self.container,
                apptainer_cache_dir=self.apptainer_cache_dir,
                apptainer_tmp_dir=self.apptainer_tmp_dir,
                apptainer_sif_cache_dir=self.apptainer_sif_cache_dir,
            )
            script_path = builder.write()
            self._announce(f"Generated Slurm script: {script_path}")
            if self.dry_run:
                self._print_dry_run_plan(sync, builder.build())
                self._announce("Dry-run complete; no job submitted.")
                return 0

            # 4. Submit job
            job_manager = JobManager(self.ssh, self.remote_dir)
            remote_script = job_manager.upload_script(script_path)
            self.job_id = job_manager.submit(remote_script)
            self._announce(f"Submitted job {self.job_id}")
            self.logger.event("JOB", "SUBMITTED", f"job_id={self.job_id}")

            if self.submit_only:
                self._announce_submit_only_details(self.job_id)
                self._announce(f"Local submission log: {self.logger.log_file}")
                return 0

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
    try:
        pipeline = SubmissionPipeline(config=config, command=command, local_dir=local_dir, **kwargs)
    except ULHPCError as exc:
        print(str(exc))
        return 1
    return pipeline.run()
