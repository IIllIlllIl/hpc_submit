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
        "df -B1 -P",
        0,
        "Filesystem     1B-blocks  Used Available Use% Mounted on\n/dev/sda 100 50 0 100% /some/path",
        "",
    )
    sync = CodeSync(
        ssh=fake_ssh,
        local_dir=str(project_dir),
        remote_dir="/some/path",
    )
    with pytest.raises(SyncDiskFullError) as exc_info:
        sync.sync()
    assert "Insufficient disk space" in str(exc_info.value)
    assert "B" in str(exc_info.value)


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


def test_disk_space_progress_message(project_dir: Path, fake_ssh):
    fake_ssh.set_response(
        "df -B1 -P",
        0,
        "Filesystem     1B-blocks  Used Available Use% Mounted on\n/dev/sda 100 50 0 100% /some/path",
        "",
    )
    messages = []
    sync = CodeSync(
        ssh=fake_ssh,
        local_dir=str(project_dir),
        remote_dir="/some/path",
        progress=messages.append,
    )
    with pytest.raises(SyncDiskFullError):
        sync.sync()
    assert any("Upload size:" in m for m in messages)
    assert any("remote free:" in m for m in messages)


def test_free_space_margin_blocks_sync(project_dir: Path, fake_ssh):
    # Project is tiny; make available space 100x larger than project but margin is 1000x.
    local_size = sum(f.stat().st_size for f in project_dir.rglob("*") if f.is_file())
    available = local_size * 100
    fake_ssh.set_response(
        "df -B1 -P",
        0,
        f"Filesystem     1B-blocks  Used Available Use% Mounted on\n"
        f"/dev/sda {available * 10} {available * 9} {available} 10% /some/path",
        "",
    )
    sync = CodeSync(
        ssh=fake_ssh,
        local_dir=str(project_dir),
        remote_dir="/some/path",
        free_space_margin=1000.0,
    )
    with pytest.raises(SyncDiskFullError):
        sync.sync()


def test_excluded_dirs_not_counted_in_upload_size(project_dir: Path, fake_ssh, rsync_success, tmp_path: Path):
    # Add a large file inside .git; with excludes it should not affect size calculation.
    git_dir = project_dir / ".git"
    git_dir.mkdir()
    (git_dir / "large_blob").write_bytes(b"x" * 1_000_000)

    # Available space is enough for non-excluded files but not if .git were counted.
    non_excluded_size = sum(
        f.stat().st_size for f in project_dir.rglob("*") if f.is_file() and ".git" not in f.parts
    )
    available = non_excluded_size * 2
    fake_ssh.set_response(
        "df -B1 -P",
        0,
        f"Filesystem     1B-blocks  Used Available Use% Mounted on\n"
        f"/dev/sda {available * 10} {available * 8} {available} 20% /some/path",
        "",
    )

    remote = tmp_path / "remote" / "sample_project"
    fake_ssh.set_response("find", 0, "2\n", "")
    sync_with_excludes = CodeSync(
        ssh=fake_ssh,
        local_dir=str(project_dir),
        remote_dir=str(remote),
        excludes=[".git"],
        rsync_cmd=rsync_success,
        free_space_margin=1.0,
    )
    # With .git excluded, space check passes and rsync succeeds.
    sync_with_excludes.sync()
    assert (remote / "main.py").exists()

    # Without excluding .git, the same available space is insufficient.
    sync_without_excludes = CodeSync(
        ssh=fake_ssh,
        local_dir=str(project_dir),
        remote_dir="/some/path",
        excludes=[],
        free_space_margin=1.0,
    )
    with pytest.raises(SyncDiskFullError):
        sync_without_excludes.sync()


def test_df_failure_does_not_block_sync(project_dir: Path, fake_ssh, rsync_success, tmp_path: Path):
    remote = tmp_path / "remote" / "sample_project"
    fake_ssh.set_response("df -B1 -P", 1, "", "df: no such file")
    fake_ssh.set_response("find", 0, "2\n", "")
    messages = []
    sync = CodeSync(
        ssh=fake_ssh,
        local_dir=str(project_dir),
        remote_dir=str(remote),
        excludes=[".git"],
        rsync_cmd=rsync_success,
        progress=messages.append,
    )
    sync.sync()
    assert (remote / "main.py").exists()
    assert any("Could not determine remote free space" in m for m in messages)
