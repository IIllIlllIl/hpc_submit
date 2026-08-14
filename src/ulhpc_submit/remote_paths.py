"""Remote layout helpers for isolated submissions and durable logs."""

from datetime import datetime
from uuid import uuid4


MANAGED_DIR = ".ulhpc_submit"


def new_run_id() -> str:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{timestamp}_{uuid4().hex[:8]}"


def run_workdir(project_root: str, run_id: str) -> str:
    return f"{project_root}/{MANAGED_DIR}/runs/{run_id}/workdir"


def log_dir(project_root: str) -> str:
    return f"{project_root}/{MANAGED_DIR}/logs"


def stdout_path(project_root: str, job_id: str) -> str:
    return f"{log_dir(project_root)}/job_{job_id}.out"


def stderr_path(project_root: str, job_id: str) -> str:
    return f"{log_dir(project_root)}/job_{job_id}.err"
