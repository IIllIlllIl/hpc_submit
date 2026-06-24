"""Command-line interface for ulhpc-submit."""

import argparse
import re
import shlex
import sys
from pathlib import Path
from typing import List, Optional

import yaml

from ulhpc_submit import __version__

from .config import (
    DEFAULTS,
    ENV_VAR_MAP,
    Config,
    build_config_from_args,
    init_config_interactive,
    load_config,
    validate_config,
)
from .errors import ConfigError, SyncNetworkError
from .logs import LogManager, create_run_logger
from .main import submit_hpc_task
from .ssh_client import SSHClient


JOB_ID_RE = re.compile(r"^[0-9]+(?:_[0-9]+)?$")


HELP_EPILOG = """
examples:
  # Basic CPU job
  ulhpc-submit python main.py

  # GPU job
  ulhpc-submit --partition gpu --gpus 1 --time 02:00:00 python train.py

  # Dry-run to inspect generated Slurm script and rsync command
  ulhpc-submit --dry-run python main.py

  # Create or overwrite the config file interactively
  ulhpc-submit --init-config

  # Inspect the merged configuration
  ulhpc-submit --show-config

configuration:
  Values are resolved in this order:
    CLI option > ULHPC_* environment variable > config file > default.

  Common environment variables:
    ULHPC_USER, ULHPC_HOST, ULHPC_PORT, ULHPC_SSH_KEY,
    ULHPC_MAX_SSH_RETRIES, ULHPC_DEFAULT_PARTITION, ULHPC_DEFAULT_TIME
"""


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="ulhpc-submit",
        description="Submit and monitor a command on the UL HPC Iris cluster.",
        usage="ulhpc-submit [options] -- COMMAND [ARGS...]",
        epilog=HELP_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="Path to YAML config file (default: ~/.config/ulhpc-submit/config.yaml)",
    )
    parser.add_argument(
        "--user",
        help="UL HPC username (overrides config and ULHPC_USER)",
    )
    parser.add_argument(
        "--host",
        help="Iris access host (overrides config and ULHPC_HOST)",
    )
    parser.add_argument(
        "--local-dir",
        default=".",
        help="Local project directory to sync (default: current directory)",
    )
    parser.add_argument(
        "--remote-dir",
        help="Remote project directory on HPC (overrides config and ULHPC_REMOTE_PROJECT_DIR)",
    )
    parser.add_argument(
        "--job-name",
        help="Slurm job name",
    )
    parser.add_argument(
        "--partition",
        help="Slurm partition (default: batch, or ULHPC_DEFAULT_PARTITION)",
    )
    parser.add_argument(
        "--nodes",
        type=int,
        help="Number of nodes",
    )
    parser.add_argument(
        "--ntasks",
        type=int,
        help="Number of tasks per node",
    )
    parser.add_argument(
        "--cpus",
        type=int,
        help="CPUs per task",
    )
    parser.add_argument(
        "--mem",
        help="Memory per node, e.g. 8G",
    )
    parser.add_argument(
        "--time",
        help="Wallclock time, e.g. 01:00:00",
    )
    parser.add_argument(
        "--gpus",
        type=int,
        help="Number of GPUs to request",
    )
    parser.add_argument(
        "--conda-env",
        help="Conda environment name to use/create",
    )
    parser.add_argument(
        "--module",
        dest="runtime_modules",
        action="append",
        default=None,
        help="Environment module to load in the Slurm job; may be repeated",
    )
    parser.add_argument(
        "--python",
        dest="python",
        help="Python executable to use for python commands in the Slurm job",
    )
    parser.add_argument(
        "--no-conda",
        action="store_true",
        help="Do not load or create a conda environment; use modules/system Python instead",
    )
    parser.add_argument(
        "--container",
        help="Apptainer/Singularity image path (.sif). The user command will be run inside the container.",
    )
    parser.add_argument(
        "--apptainer-cache-dir",
        help="Remote APPTAINER_CACHEDIR for Apptainer layer cache",
    )
    parser.add_argument(
        "--apptainer-tmp-dir",
        help="Remote APPTAINER_TMPDIR for Apptainer temporary build data",
    )
    parser.add_argument(
        "--apptainer-sif-cache-dir",
        help="Remote directory for project-managed SIF cache metadata",
    )
    parser.add_argument(
        "--env-file",
        help="Ignored for compatibility; environment.yml/requirements.txt are auto-detected",
    )
    parser.add_argument(
        "--no-sync",
        action="store_true",
        help="Skip code sync",
    )
    parser.add_argument(
        "--stage-data",
        action="append",
        default=None,
        metavar="LOCAL:REMOTE",
        help="Sync an external data directory to a remote staging path; may be repeated",
    )
    parser.add_argument(
        "--link-as",
        action="append",
        default=None,
        metavar="PROJECT_PATH",
        help="Project-relative symlink path for the corresponding --stage-data entry",
    )
    parser.add_argument(
        "--persistent-output",
        action="append",
        default=None,
        metavar="PROJECT_PATH:REMOTE",
        help="Link a project output/state path to a persistent remote directory; may be repeated",
    )
    extra_policy = parser.add_mutually_exclusive_group()
    extra_policy.add_argument(
        "--sync-strict",
        dest="sync_remote_extra_policy",
        action="store_const",
        const="strict",
        default=None,
        help="Fail when remote workdir contains extra files after sync",
    )
    extra_policy.add_argument(
        "--remote-ignore-extra",
        dest="sync_remote_extra_policy",
        action="store_const",
        const="ignore",
        help="Ignore extra remote files after sync when local files are present",
    )
    extra_policy.add_argument(
        "--remote-clean-excluded",
        dest="sync_remote_extra_policy",
        action="store_const",
        const="clean",
        help="Delete extra remote files reported by the integrity check",
    )
    parser.add_argument(
        "--full-logs",
        action="store_true",
        help="Download full remote logs instead of tailing",
    )
    parser.add_argument(
        "--submit-only",
        "--detach",
        dest="submit_only",
        action="store_true",
        help="Submit the Slurm job, print job details, and exit without monitoring",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate script and print rsync command without submitting",
    )
    parser.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="Print a structured JSON result to stdout for wrapper tooling",
    )
    parser.add_argument(
        "--pre-sync-command",
        help="Local shell command to run before rsync starts",
    )
    parser.add_argument(
        "--pre-run-command",
        help="Shell command injected into the Slurm script before the user command",
    )
    parser.add_argument(
        "--post-run-command",
        help="Shell command injected into the Slurm script after the user command",
    )
    parser.add_argument(
        "--on-failure-command",
        help="Shell command injected into the Slurm script when the user command fails",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print more detailed progress messages",
    )
    parser.add_argument(
        "--max-ssh-retries",
        type=int,
        help="Maximum SSH connection attempts (default: 1, fail-fast)",
    )
    parser.add_argument(
        "--sync-free-space-margin",
        type=float,
        help="Minimum free-space multiplier for remote sync (default: 1.1)",
    )
    parser.add_argument(
        "--test-connection",
        action="store_true",
        help="Verify SSH connectivity to the access node once and exit",
    )
    init_or_show = parser.add_mutually_exclusive_group()
    init_or_show.add_argument(
        "--init-config",
        "--setup",
        dest="init_config",
        action="store_true",
        help="Create ~/.config/ulhpc-submit/config.yaml interactively",
    )
    init_or_show.add_argument(
        "--show-config",
        dest="show_config",
        action="store_true",
        help="Print the merged configuration and exit",
    )
    parser.add_argument(
        "--explain",
        action="store_true",
        help="With --show-config, include defaults and environment variable mappings",
    )
    parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="Command to run on HPC, e.g. python main.py",
    )

    args = parser.parse_args(argv)
    if args.command and args.command[0] == "--":
        args.command = args.command[1:]
    if (
        not args.command
        and not args.init_config
        and not args.show_config
        and not args.test_connection
    ):
        parser.error("Please provide a command to run, e.g. ulhpc-submit python main.py")
    return args


def _config_schema() -> dict:
    return {
        key: {
            "default": value,
            "env": ENV_VAR_MAP.get(key, ""),
        }
        for key, value in DEFAULTS.items()
    }


def _show_config_with_explain(config: Config) -> dict:
    return {
        "config": config.__dict__,
        "schema": _config_schema(),
        "precedence": [
            "CLI option",
            "ULHPC_* environment variable",
            "config file",
            "built-in default",
        ],
    }


def _add_connection_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", type=Path)
    parser.add_argument("--user")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--remote-dir")
    parser.add_argument("--partition")
    parser.add_argument("--max-ssh-retries", type=int)
    parser.add_argument("--ssh-key")


def parse_doctor_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="ulhpc-submit doctor",
        description="Run fail-fast local and remote checks before submission.",
    )
    _add_connection_args(parser)
    parser.add_argument(
        "--module",
        dest="runtime_modules",
        action="append",
        default=None,
        help="Remote environment module to check; may be repeated",
    )
    return parser.parse_args(argv)


def parse_fetch_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="ulhpc-submit fetch",
        description="Fetch stdout/stderr for an existing Slurm job.",
    )
    _add_connection_args(parser)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--full-logs", action="store_true")
    return parser.parse_args(argv)


def _run_doctor(argv: Optional[List[str]] = None) -> int:
    args = parse_doctor_args(argv)
    config = build_config_from_args(args, warn_missing=True)
    try:
        validate_config(config)
    except ConfigError as exc:
        print(f"[ulhpc-submit] ERROR {exc}", file=sys.stderr)
        return 2

    ssh = SSHClient(
        host=config.host,
        port=config.port,
        user=config.user,
        key_path=config.ssh_key,
        key_passphrase=config.ssh_key_passphrase,
        max_retries=config.max_ssh_retries,
    )
    try:
        ssh.connect()
        print(f"[ulhpc-submit] SSH OK: {config.user}@{config.host}:{config.port}")
        remote_dir = ssh.expand_remote_path(
            args.remote_dir or config.expand_remote_project_dir("doctor")
        )
        quoted_remote_dir = shlex.quote(remote_dir)
        rc, out, err = ssh.exec_command(
            f"mkdir -p {quoted_remote_dir} && test -w {quoted_remote_dir}"
        )
        if rc != 0:
            print(f"[ulhpc-submit] ERROR remote directory is not writable: {err}", file=sys.stderr)
            return 1
        print(f"[ulhpc-submit] Remote directory writable: {remote_dir}")

        modules = args.runtime_modules or config.runtime_modules
        for module in modules:
            rc, _, err = ssh.exec_command(f"module avail {shlex.quote(module)}")
            if rc != 0:
                print(f"[ulhpc-submit] ERROR module not available: {module}: {err}", file=sys.stderr)
                return 1
            print(f"[ulhpc-submit] Module available: {module}")

        partition = args.partition or config.default_partition
        rc, _, err = ssh.exec_command(f"sinfo -h -p {shlex.quote(partition)}")
        if rc != 0:
            print(f"[ulhpc-submit] ERROR partition check failed: {partition}: {err}", file=sys.stderr)
            return 1
        print(f"[ulhpc-submit] Partition visible: {partition}")
        return 0
    except SyncNetworkError as exc:
        print(f"[ulhpc-submit] ERROR {exc}", file=sys.stderr)
        return 2
    finally:
        ssh.close()


def _run_fetch(argv: Optional[List[str]] = None) -> int:
    args = parse_fetch_args(argv)
    config = build_config_from_args(args, warn_missing=True)
    try:
        validate_config(config)
    except ConfigError as exc:
        print(f"[ulhpc-submit] ERROR {exc}", file=sys.stderr)
        return 2
    if not args.remote_dir:
        print("[ulhpc-submit] ERROR fetch requires --remote-dir", file=sys.stderr)
        return 2
    if not JOB_ID_RE.match(args.job_id):
        print("[ulhpc-submit] ERROR fetch --job-id must be a Slurm job id, e.g. 123456", file=sys.stderr)
        return 2

    ssh = SSHClient(
        host=config.host,
        port=config.port,
        user=config.user,
        key_path=config.ssh_key,
        key_passphrase=config.ssh_key_passphrase,
        max_retries=config.max_ssh_retries,
    )
    try:
        ssh.connect()
        remote_dir = ssh.expand_remote_path(args.remote_dir)
        logger = create_run_logger(config.resolved_log_dir(), f"fetch_{args.job_id}")
        manager = LogManager(
            ssh=ssh,
            remote_dir=remote_dir,
            job_id=args.job_id,
            run_logger=logger,
            full_logs=args.full_logs,
        )
        stdout, stderr = manager.fetch()
        manager.merge(stdout, stderr)
        print(f"[ulhpc-submit] Fetched logs for job {args.job_id}: {logger.log_file}")
        return 0
    except SyncNetworkError as exc:
        print(f"[ulhpc-submit] ERROR {exc}", file=sys.stderr)
        return 2
    finally:
        ssh.close()


def main(argv: Optional[List[str]] = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if argv and argv[0] == "doctor":
        return _run_doctor(argv[1:])
    if argv and argv[0] == "fetch":
        return _run_fetch(argv[1:])
    if argv and argv[0] == "config-schema":
        print(yaml.safe_dump(_config_schema(), sort_keys=False, default_flow_style=False).rstrip())
        return 0

    args = parse_args(argv)

    if args.init_config:
        try:
            init_config_interactive(args.config)
        except (OSError, ValueError) as exc:
            print(f"[ulhpc-submit] ERROR failed to create config: {exc}", file=sys.stderr)
            return 2
        return 0

    if args.show_config:
        config = load_config(args.config)
        data = _show_config_with_explain(config) if args.explain else config.__dict__
        print(yaml.safe_dump(data, sort_keys=False, default_flow_style=False).rstrip())
        return 0

    if args.test_connection:
        config = build_config_from_args(args, warn_missing=True)
        try:
            validate_config(config)
        except ConfigError as exc:
            print(f"[ulhpc-submit] ERROR {exc}", file=sys.stderr)
            return 2
        try:
            ssh = SSHClient(
                host=config.host,
                port=config.port,
                user=config.user,
                key_path=config.ssh_key,
                key_passphrase=config.ssh_key_passphrase,
                max_retries=1,
            )
            ssh.connect()
            ssh.exec_command("true")
            ssh.close()
            print(
                f"[ulhpc-submit] SSH connectivity test succeeded: "
                f"{config.user}@{config.host}:{config.port}"
            )
            return 0
        except SyncNetworkError as exc:
            print(f"[ulhpc-submit] ERROR {exc}", file=sys.stderr)
            return 2

    config = build_config_from_args(args, warn_missing=True)

    try:
        validate_config(config)
    except ConfigError as exc:
        print(f"[ulhpc-submit] ERROR {exc}", file=sys.stderr)
        return 2

    # Resolve local directory
    local_dir = Path(args.local_dir).resolve()
    if not local_dir.is_dir():
        print(f"[ulhpc-submit] ERROR local directory does not exist: {local_dir}", file=sys.stderr)
        return 2

    return submit_hpc_task(
        config=config,
        command=args.command,
        local_dir=str(local_dir),
        remote_dir=args.remote_dir,
        job_name=args.job_name,
        partition=args.partition,
        nodes=args.nodes,
        ntasks=args.ntasks,
        cpus=args.cpus,
        mem=args.mem,
        time=args.time,
        gpus=args.gpus,
        conda_env=args.conda_env,
        runtime_modules=args.runtime_modules,
        python_executable=args.python,
        use_conda=False if args.no_conda else None,
        container=args.container,
        apptainer_cache_dir=args.apptainer_cache_dir,
        apptainer_tmp_dir=args.apptainer_tmp_dir,
        apptainer_sif_cache_dir=args.apptainer_sif_cache_dir,
        no_sync=args.no_sync,
        stage_data=args.stage_data,
        link_as=args.link_as,
        persistent_output=args.persistent_output,
        sync_remote_extra_policy=args.sync_remote_extra_policy,
        pre_sync_command=args.pre_sync_command,
        pre_run_command=args.pre_run_command,
        post_run_command=args.post_run_command,
        on_failure_command=args.on_failure_command,
        full_logs=args.full_logs,
        submit_only=args.submit_only,
        dry_run=args.dry_run,
        json_output=args.json_output,
    )


if __name__ == "__main__":
    raise SystemExit(main())
