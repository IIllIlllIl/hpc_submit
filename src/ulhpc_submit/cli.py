"""Command-line interface for ulhpc-submit."""

import argparse
import sys
from pathlib import Path
from typing import List, Optional

import yaml

from ulhpc_submit import __version__

from .config import (
    Config,
    build_config_from_args,
    init_config_interactive,
    load_config,
    validate_config,
)
from .errors import ConfigError, SyncNetworkError
from .main import submit_hpc_task
from .ssh_client import SSHClient


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
        "--container",
        help="Apptainer/Singularity image path (.sif). The user command will be run inside the container.",
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
        "--full-logs",
        action="store_true",
        help="Download full remote logs instead of tailing",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate script and print rsync command without submitting",
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
        "command",
        nargs=argparse.REMAINDER,
        help="Command to run on HPC, e.g. python main.py",
    )

    args = parser.parse_args(argv)
    if args.command and args.command[0] == "--":
        args.command = args.command[1:]
    if not args.command and not args.init_config and not args.show_config and not args.test_connection:
        parser.error("Please provide a command to run, e.g. ulhpc-submit python main.py")
    return args


def main(argv: Optional[List[str]] = None) -> int:
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
        print(yaml.safe_dump(config.__dict__, sort_keys=False, default_flow_style=False).rstrip())
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
        container=args.container,
        no_sync=args.no_sync,
        full_logs=args.full_logs,
        dry_run=args.dry_run,
        sync_free_space_margin=args.sync_free_space_margin,
    )


if __name__ == "__main__":
    raise SystemExit(main())
