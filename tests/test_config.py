"""Tests for configuration loading."""

from pathlib import Path

import pytest
import yaml

from ulhpc_submit.config import (
    DEFAULTS,
    Config,
    build_config_from_args,
    init_config_interactive,
    load_config,
    load_env_overrides,
    validate_config,
)
from ulhpc_submit.errors import ConfigError


class DummyArgs:
    config = None
    host = None
    port = None
    user = None
    remote_dir = None
    partition = None
    nodes = None
    ntasks = None
    cpus = None
    mem = None
    time = None
    conda_module = None
    python_module = None
    poll_interval = None
    pending_timeout = None
    max_ssh_retries = None
    ssh_key = None


def test_load_default_config(tmp_path: Path):
    cfg = load_config(tmp_path / "nonexistent.yaml")
    assert cfg.host == DEFAULTS["host"]
    assert cfg.port == DEFAULTS["port"]


def test_load_yaml_config(tmp_path: Path):
    path = tmp_path / "config.yaml"
    path.write_text(
        yaml.safe_dump({"user": "testuser", "default_partition": "gpu"}),
        encoding="utf-8",
    )
    cfg = load_config(path)
    assert cfg.user == "testuser"
    assert cfg.default_partition == "gpu"
    assert cfg.host == DEFAULTS["host"]


def test_cli_overrides():
    args = DummyArgs()
    args.remote_dir = "~/override"
    args.partition = "bigmem"
    cfg = build_config_from_args(args)
    assert cfg.remote_project_dir == "~/override"
    assert cfg.default_partition == "bigmem"


def test_expand_remote_project_dir():
    cfg = Config(**DEFAULTS)
    # Tilde must be kept unexpanded so the remote shell expands it on Iris.
    assert cfg.expand_remote_project_dir("foo") == "~/hpc_runs/foo"


def test_config_file_permissions_are_restricted(tmp_path: Path):
    path = tmp_path / "config.yaml"
    path.write_text(
        yaml.safe_dump({"user": "testuser"}),
        encoding="utf-8",
    )
    # Start with permissive mode to verify load_config tightens it.
    path.chmod(0o644)
    parent = path.parent
    parent.chmod(0o755)

    load_config(path)

    assert path.stat().st_mode & 0o777 == 0o600
    assert parent.stat().st_mode & 0o777 == 0o700


def test_load_env_overrides(monkeypatch):
    monkeypatch.setenv("ULHPC_USER", "envuser")
    monkeypatch.setenv("ULHPC_HOST", "envhost")
    monkeypatch.setenv("ULHPC_PORT", "9999")
    monkeypatch.setenv("ULHPC_DEFAULT_PARTITION", "envpart")

    overrides = load_env_overrides()
    assert overrides["user"] == "envuser"
    assert overrides["host"] == "envhost"
    assert overrides["port"] == 9999
    assert overrides["default_partition"] == "envpart"


def test_env_var_precedence_between_file_and_cli(tmp_path: Path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text(
        yaml.safe_dump({"user": "fileuser", "host": "filehost"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("ULHPC_USER", "envuser")

    args = DummyArgs()
    args.config = path
    cfg = build_config_from_args(args)
    assert cfg.user == "envuser"  # env overrides file
    assert cfg.host == "filehost"  # file provides default

    args2 = DummyArgs()
    args2.config = path
    args2.user = "cliuser"
    cfg2 = build_config_from_args(args2)
    assert cfg2.user == "cliuser"  # CLI overrides env


def test_validate_config_rejects_placeholder_user():
    cfg = Config(**DEFAULTS)
    with pytest.raises(ConfigError) as exc_info:
        validate_config(cfg)
    assert "user" in str(exc_info.value)


def test_validate_config_rejects_missing_host():
    data = dict(DEFAULTS)
    data["user"] = "testuser"
    data["host"] = ""
    cfg = Config(**data)
    with pytest.raises(ConfigError) as exc_info:
        validate_config(cfg)
    assert "host" in str(exc_info.value)


def test_default_max_ssh_retries_is_one():
    assert DEFAULTS["max_ssh_retries"] == 1
    cfg = Config(**DEFAULTS)
    assert cfg.max_ssh_retries == 1


def test_env_override_max_ssh_retries(monkeypatch):
    monkeypatch.setenv("ULHPC_MAX_SSH_RETRIES", "3")
    overrides = load_env_overrides()
    assert overrides["max_ssh_retries"] == 3


def test_cli_override_max_ssh_retries():
    args = DummyArgs()
    args.max_ssh_retries = 3
    cfg = build_config_from_args(args)
    assert cfg.max_ssh_retries == 3


def test_cli_max_ssh_retries_precedence(tmp_path: Path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"user": "fileuser", "max_ssh_retries": 5}), encoding="utf-8")
    monkeypatch.setenv("ULHPC_MAX_SSH_RETRIES", "2")

    args = DummyArgs()
    args.config = path
    cfg = build_config_from_args(args)
    assert cfg.max_ssh_retries == 2  # env overrides file

    args2 = DummyArgs()
    args2.config = path
    args2.max_ssh_retries = 3
    cfg2 = build_config_from_args(args2)
    assert cfg2.max_ssh_retries == 3  # CLI overrides env and file


def test_validate_config_rejects_non_positive_max_ssh_retries():
    data = dict(DEFAULTS)
    data["user"] = "testuser"
    data["max_ssh_retries"] = 0
    cfg = Config(**data)
    with pytest.raises(ConfigError) as exc_info:
        validate_config(cfg)
    assert "max_ssh_retries" in str(exc_info.value)


def test_validate_config_rejects_string_max_ssh_retries():
    data = dict(DEFAULTS)
    data["user"] = "testuser"
    data["max_ssh_retries"] = "abc"
    cfg = Config(**data)
    with pytest.raises(ConfigError) as exc_info:
        validate_config(cfg)
    assert "max_ssh_retries" in str(exc_info.value)


def test_validate_config_accepts_valid_config():
    data = dict(DEFAULTS)
    data["user"] = "testuser"
    cfg = Config(**data)
    validate_config(cfg)  # should not raise


def test_init_config_interactive(tmp_path: Path, monkeypatch):
    path = tmp_path / "config.yaml"
    inputs = iter(["myuser", "", "", "", "", ""])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(inputs))

    written = init_config_interactive(path)
    assert written == path
    assert path.exists()

    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert loaded["user"] == "myuser"
    assert loaded["host"] == DEFAULTS["host"]
    assert loaded["port"] == DEFAULTS["port"]
    assert path.stat().st_mode & 0o777 == 0o600


def test_load_config_warns_when_missing(tmp_path: Path, capsys):
    missing = tmp_path / "nonexistent.yaml"
    load_config(missing, warn_missing=True)
    captured = capsys.readouterr()
    assert "No config file found" in captured.err
    assert "--init-config" in captured.err
