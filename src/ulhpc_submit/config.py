"""Configuration loading and merging.

Supports a user YAML file at ~/.config/ulhpc-submit/config.yaml, environment
variables (ULHPC_*), and CLI overrides. CLI values take precedence over
environment variables, which take precedence over config file values, which
take precedence over defaults.
"""

import getpass
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from .errors import ConfigError


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
    "runtime_modules": [],
    "python_executable": "python",
    "use_conda": True,
    "data_mounts": [],
    "persistent_outputs": [],
    "apptainer_cache_dir": None,
    "apptainer_tmp_dir": None,
    "apptainer_sif_cache_dir": None,
    "sync_remote_extra_policy": "strict",
    "sync_excludes": [".git", "__pycache__", "*.pyc", ".env", "node_modules", ".ulhpc_submit", "Dockerfile"],
    "sync_free_space_margin": 1.1,
    "poll_interval": 10,
    "pending_timeout": 3600,
    "max_ssh_retries": 1,
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
    runtime_modules: List[str] = field(default_factory=list)
    python_executable: str = "python"
    use_conda: bool = True
    data_mounts: List[Dict[str, str]] = field(default_factory=list)
    persistent_outputs: List[Dict[str, str]] = field(default_factory=list)
    apptainer_cache_dir: Optional[str] = None
    apptainer_tmp_dir: Optional[str] = None
    apptainer_sif_cache_dir: Optional[str] = None
    sync_remote_extra_policy: str = "strict"
    sync_excludes: List[str] = field(default_factory=list)
    sync_free_space_margin: float = 1.1
    poll_interval: int = 10
    pending_timeout: int = 3600
    max_ssh_retries: int = 1
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


ENV_VAR_MAP: Dict[str, str] = {
    "host": "ULHPC_HOST",
    "port": "ULHPC_PORT",
    "user": "ULHPC_USER",
    "remote_project_dir": "ULHPC_REMOTE_PROJECT_DIR",
    "default_partition": "ULHPC_DEFAULT_PARTITION",
    "default_nodes": "ULHPC_DEFAULT_NODES",
    "default_ntasks": "ULHPC_DEFAULT_NTASKS",
    "default_cpus_per_task": "ULHPC_DEFAULT_CPUS_PER_TASK",
    "default_mem": "ULHPC_DEFAULT_MEM",
    "default_time": "ULHPC_DEFAULT_TIME",
    "conda_module": "ULHPC_CONDA_MODULE",
    "python_module": "ULHPC_PYTHON_MODULE",
    "container_module": "ULHPC_CONTAINER_MODULE",
    "python_executable": "ULHPC_PYTHON",
    "apptainer_cache_dir": "ULHPC_APPTAINER_CACHE_DIR",
    "apptainer_tmp_dir": "ULHPC_APPTAINER_TMP_DIR",
    "apptainer_sif_cache_dir": "ULHPC_APPTAINER_SIF_CACHE_DIR",
    "sync_free_space_margin": "ULHPC_SYNC_FREE_SPACE_MARGIN",
    "poll_interval": "ULHPC_POLL_INTERVAL",
    "pending_timeout": "ULHPC_PENDING_TIMEOUT",
    "max_ssh_retries": "ULHPC_MAX_SSH_RETRIES",
    "log_dir": "ULHPC_LOG_DIR",
    "ssh_key": "ULHPC_SSH_KEY",
    "ssh_key_passphrase": "ULHPC_SSH_KEY_PASSPHRASE",
}


def load_env_overrides() -> Dict[str, Any]:
    """Read ULHPC_* environment variables and return typed overrides."""
    overrides: Dict[str, Any] = {}
    for cfg_name, env_name in ENV_VAR_MAP.items():
        value = os.environ.get(env_name)
        if value is None:
            continue
        if cfg_name in {
            "port",
            "default_nodes",
            "default_ntasks",
            "default_cpus_per_task",
            "poll_interval",
            "pending_timeout",
            "max_ssh_retries",
        }:
            try:
                value = int(value)
            except ValueError:
                continue
        elif cfg_name == "sync_free_space_margin":
            try:
                value = float(value)
            except ValueError:
                continue
        overrides[cfg_name] = value
    return overrides


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


def validate_config(config: Config) -> None:
    """Validate required configuration fields before connecting to HPC.

    Raises:
        ConfigError: if a required field is missing or still a placeholder.
    """
    if not config.host:
        raise ConfigError(
            "Missing required config value: host.",
            suggestion="Set 'host' via --host, ULHPC_HOST env var, or run 'ulhpc-submit --init-config'.",
        )
    if not config.user or config.user == "your_username":
        raise ConfigError(
            "Missing or placeholder config value: user.",
            suggestion="Set 'user' via --user, ULHPC_USER env var, or run 'ulhpc-submit --init-config'.",
        )
    if not isinstance(config.max_ssh_retries, int) or config.max_ssh_retries < 1:
        raise ConfigError(
            "max_ssh_retries must be an integer >= 1.",
            suggestion="Set --max-ssh-retries to a positive integer, e.g. 1 for fail-fast or 3 to tolerate transient issues.",
        )


def _prompt(default: str, prompt_text: str) -> str:
    """Prompt the user with a default value."""
    value = input(f"{prompt_text} [{default}]: ").strip()
    return value if value else default


def init_config_interactive(path: Optional[Path] = None) -> Path:
    """Interactively create a config file at the standard location.

    Returns:
        Path to the written config file.
    """
    cfg_path = path or _config_file_path()
    cfg_path.parent.mkdir(parents=True, exist_ok=True)

    default_user = getpass.getuser()
    default_host = DEFAULTS["host"]
    default_port = str(DEFAULTS["port"])
    default_partition = DEFAULTS["default_partition"]
    default_time = DEFAULTS["default_time"]

    print(f"Creating config file: {cfg_path}")
    user = _prompt(default_user, "UL HPC username")
    host = _prompt(default_host, "Iris access host")
    port = int(_prompt(default_port, "SSH port"))
    ssh_key = _prompt("", "Path to SSH private key (optional, leave empty for ssh-agent)")
    partition = _prompt(default_partition, "Default Slurm partition")
    time = _prompt(default_time, "Default wallclock time")

    data = {
        "user": user,
        "host": host,
        "port": port,
        "default_partition": partition,
        "default_time": time,
    }
    if ssh_key:
        data["ssh_key"] = os.path.expanduser(ssh_key)

    with cfg_path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, sort_keys=False, default_flow_style=False)

    _ensure_config_permissions(cfg_path)
    print(f"Config written to {cfg_path} with restricted permissions.")
    return cfg_path


def load_config(path: Optional[Path] = None, warn_missing: bool = False) -> Config:
    """Load configuration from file, falling back to defaults.

    Args:
        path: Config file path. Defaults to ~/.config/ulhpc-submit/config.yaml.
        warn_missing: If True, print a first-run hint to stderr when the file
            does not exist.
    """
    cfg_path = path or _config_file_path()
    _ensure_config_permissions(cfg_path)
    data: Dict[str, Any] = {}
    if cfg_path.exists():
        with cfg_path.open("r", encoding="utf-8") as fh:
            loaded = yaml.safe_load(fh)
            if isinstance(loaded, dict):
                data = loaded
    elif warn_missing:
        print(
            f"[ulhpc-submit] No config file found at {cfg_path}. "
            "Run 'ulhpc-submit --init-config' to create one.",
            file=sys.stderr,
        )
    merged = _merge_dicts(DEFAULTS, data)
    return Config(**merged)


def build_config_from_args(args: Any, warn_missing: bool = False) -> Config:
    """Merge config file, environment variables, and argparse overrides."""
    file_cfg = load_config(getattr(args, "config", None), warn_missing=warn_missing)

    env_overrides = load_env_overrides()
    cli_overrides: Dict[str, Any] = {}
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
        "python": "python_executable",
        "apptainer_cache_dir": "apptainer_cache_dir",
        "apptainer_tmp_dir": "apptainer_tmp_dir",
        "apptainer_sif_cache_dir": "apptainer_sif_cache_dir",
        "sync_free_space_margin": "sync_free_space_margin",
        "poll_interval": "poll_interval",
        "pending_timeout": "pending_timeout",
        "max_ssh_retries": "max_ssh_retries",
        "ssh_key": "ssh_key",
    }
    for arg_name, cfg_name in arg_map.items():
        value = getattr(args, arg_name, None)
        if value is not None:
            cli_overrides[cfg_name] = value

    merged = _merge_dicts(file_cfg.__dict__, env_overrides)
    merged = _merge_dicts(merged, cli_overrides)
    return Config(**merged)
