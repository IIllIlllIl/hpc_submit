"""Command-line interface for ulhpc-submit."""

import argparse
import os
import sys
from pathlib import Path
from typing import List, Optional

from .config import Config, build_config_from_args
from .main import submit_hpc_task


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="ulhpc-submit",
        description="Submit and monitor a command on the UL HPC Iris cluster.",
        usage="ulhpc-submit [options] -- COMMAND [ARGS...]",
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="Path to YAML config file (default: ~/.config/ulhpc-submit/config.yaml)",
    )
    parser.add_argument(
        "--local-dir",
        default=".",
        help="Local project directory to sync (default: current directory)",
    )
    parser.add_argument(
        "--remote-dir",
        help="Remote project directory on HPC (overrides config)",
    )
    parser.add_argument(
        "--job-name",
        help="Slurm job name",
    )
    parser.add_argument(
        "--partition",
        help="Slurm partition (default: batch)",
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
        "command",
        nargs=argparse.REMAINDER,
        help="Command to run on HPC, e.g. python main.py",
    )

    args = parser.parse_args(argv)
    if args.command and args.command[0] == "--":
        args.command = args.command[1:]
    if not args.command:
        parser.error("Please provide a command to run, e.g. ulhpc-submit python main.py")
    return args


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    config = build_config_from_args(args)

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
    )


if __name__ == "__main__":
    raise SystemExit(main())
