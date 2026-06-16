"""Tests for configuration loading."""

from pathlib import Path

import pytest
import yaml

from ulhpc_submit.config import DEFAULTS, Config, build_config_from_args, load_config


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
