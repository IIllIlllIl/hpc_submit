"""Tests for code synchronization."""

from pathlib import Path

import pytest

from ulhpc_submit.errors import (
    SyncDiskFullError,
    SyncIntegrityError,
    SyncNetworkError,
    SyncPermissionError,
)
from ulhpc_submit.sync import CodeSync


def test_successful_sync(project_dir: Path, tmp_config, fake_ssh, rsync_success, tmp_path: Path):
    remote = tmp_path / "remote" / "sample_project"
    fake_ssh.set_response("find", 0, "2\n", "")
    sync = CodeSync(
        ssh=fake_ssh,
        local_dir=str(project_dir),
        remote_dir=str(remote),
        excludes=[".git"],
        rsync_cmd=rsync_success,
    )
    sync.sync()
    assert (remote / "main.py").exists()
    assert (remote / "requirements.txt").exists()


def test_remote_not_writable(project_dir: Path, fake_ssh):
    fake_ssh.set_response("test -d", 0, "EXISTS\n", "")
    # No WRITABLE in output means not writable
    sync = CodeSync(
        ssh=fake_ssh,
        local_dir=str(project_dir),
        remote_dir="/some/path",
    )
    with pytest.raises(SyncPermissionError):
        sync.sync()


def test_disk_full(project_dir: Path, fake_ssh):
    fake_ssh.set_response(
        "df -h",
        0,
        "Filesystem Size Used Avail Use% Mounted\n/dev/sda 100G 99G 1G 99% /some/path",
        "",
    )
    sync = CodeSync(
        ssh=fake_ssh,
        local_dir=str(project_dir),
        remote_dir="/some/path",
    )
    with pytest.raises(SyncDiskFullError):
        sync.sync()


def test_rsync_network_error(project_dir: Path, fake_ssh, rsync_fail_network):
    sync = CodeSync(
        ssh=fake_ssh,
        local_dir=str(project_dir),
        remote_dir="/some/path",
        rsync_cmd=rsync_fail_network,
    )
    with pytest.raises(SyncNetworkError):
        sync.sync()


def test_rsync_command_uses_ssh_key(project_dir: Path, fake_ssh):
    fake_ssh.key_path = "~/.ssh/id_rsa"
    sync = CodeSync(
        ssh=fake_ssh,
        local_dir=str(project_dir),
        remote_dir="/some/path",
    )
    cmd = sync.sync_dry_run()
    assert "-i" in cmd
    assert "~/.ssh/id_rsa" in cmd or "/.ssh/id_rsa" in cmd


def test_integrity_mismatch(project_dir: Path, fake_ssh, rsync_success, tmp_path: Path):
    remote = tmp_path / "remote" / "sample_project"
    sync = CodeSync(
        ssh=fake_ssh,
        local_dir=str(project_dir),
        remote_dir=str(remote),
        rsync_cmd=rsync_success,
    )
    # After rsync, fake find returns 5 even though 2 files synced.
    fake_ssh.set_response("find", 0, "5\n", "")
    with pytest.raises(SyncIntegrityError):
        sync.sync()
