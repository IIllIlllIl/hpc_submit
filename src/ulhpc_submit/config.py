"""Configuration loading and merging.

Supports a user YAML file at ~/.config/ulhpc-submit/config.yaml and CLI
overrides. CLI values take precedence over config file values, which take
precedence over defaults.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml


DEFAULTS: Dict[str, Any] = {
    "host": "access-iris.uni.lu",
    "port": 8022,
    "user": "your_username",
    "remote_project_dir": "~/hpc_runs/{project_name}",
    "default_partition": "batch",
    "default_nodes": 1,
    "default_ntasks": 1,
    "default_cpus_per_task": 1,
    "default_mem": "4G",
    "default_time": "01:00:00",
    "conda_module": "miniconda3",
    "python_module": "lang/Python/3.11",
    "container_module": "apptainer",
    "sync_excludes": [".git", "__pycache__", "*.pyc", ".env", "node_modules", ".ulhpc_submit", "Dockerfile"],
    "poll_interval": 10,
    "pending_timeout": 3600,
    "log_dir": "~/.local/share/ulhpc-submit/runs",
    "ssh_key": None,
    "ssh_key_passphrase": None,
}


@dataclass
class Config:
    host: str
    port: int
    user: str
    remote_project_dir: str
    default_partition: str
    default_nodes: int
    default_ntasks: int
    default_cpus_per_task: int
    default_mem: str
    default_time: str
    conda_module: str
    python_module: str
    container_module: str = "apptainer"
    sync_excludes: List[str] = field(default_factory=list)
    poll_interval: int = 10
    pending_timeout: int = 3600
    log_dir: str = "~/.local/share/ulhpc-submit/runs"
    ssh_key: Optional[str] = None
    ssh_key_passphrase: Optional[str] = None

    @property
    def remote_host_spec(self) -> str:
        """User@host string used by rsync/ssh."""
        return f"{self.user}@{self.host}"

    def expand_remote_project_dir(self, project_name: str) -> str:
        """Expand the project name placeholder; keep ~ for remote shell expansion."""
        return self.remote_project_dir.format(project_name=project_name)

    def resolved_log_dir(self) -> Path:
        return Path(os.path.expanduser(self.log_dir))


def _merge_dicts(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Shallow merge; override wins."""
    result = dict(base)
    for key, value in override.items():
        if value is not None:
            result[key] = value
    return result


def _config_file_path() -> Path:
    """Standard config location."""
    return Path.home() / ".config" / "ulhpc-submit" / "config.yaml"


def _ensure_config_permissions(path: Path) -> None:
    """Restrict config file and parent directory to the owner.

    The file may contain SSH keys or passphrases; keep it private.
    """
    if path.exists():
        try:
            path.chmod(0o600)
        except OSError:
            pass
    parent = path.parent
    if parent.exists():
        try:
            parent.chmod(0o700)
        except OSError:
            pass


def load_config(path: Optional[Path] = None) -> Config:
    """Load configuration from file, falling back to defaults."""
    cfg_path = path or _config_file_path()
    _ensure_config_permissions(cfg_path)
    data: Dict[str, Any] = {}
    if cfg_path.exists():
        with cfg_path.open("r", encoding="utf-8") as fh:
            loaded = yaml.safe_load(fh)
            if isinstance(loaded, dict):
                data = loaded
    merged = _merge_dicts(DEFAULTS, data)
    return Config(**merged)


def build_config_from_args(args: Any) -> Config:
    """Merge config file with argparse namespace overrides."""
    file_cfg = load_config(getattr(args, "config", None))

    overrides: Dict[str, Any] = {}
    arg_map = {
        "host": "host",
        "port": "port",
        "user": "user",
        "remote_dir": "remote_project_dir",
        "partition": "default_partition",
        "nodes": "default_nodes",
        "ntasks": "default_ntasks",
        "cpus": "default_cpus_per_task",
        "mem": "default_mem",
        "time": "default_time",
        "conda_module": "conda_module",
        "python_module": "python_module",
        "poll_interval": "poll_interval",
        "pending_timeout": "pending_timeout",
        "ssh_key": "ssh_key",
    }
    for arg_name, cfg_name in arg_map.items():
        value = getattr(args, arg_name, None)
        if value is not None:
            overrides[cfg_name] = value

    merged = _merge_dicts(file_cfg.__dict__, overrides)
    return Config(**merged)
