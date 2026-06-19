"""Tests for SSH client wrapper."""

import pytest

from ulhpc_submit.errors import SyncNetworkError
from ulhpc_submit.ssh_client import SSHClient


def test_ssh_client_default_max_retries_is_one():
    ssh = SSHClient("host", 22, "user")
    assert ssh.max_retries == 1


def test_ssh_client_tracks_connection(monkeypatch):
    calls = []

    class DummyClient:
        def __init__(self, **kwargs):
            calls.append("init")

        def load_system_host_keys(self):
            pass

        def set_missing_host_key_policy(self, policy):
            pass

        def connect(self, **kwargs):
            calls.append("connect")

        def exec_command(self, command):
            class Stream:
                def read(self):
                    return b""

                class channel:
                    @staticmethod
                    def recv_exit_status():
                        return 0

            return None, Stream(), Stream()

    monkeypatch.setattr("ulhpc_submit.ssh_client.paramiko.SSHClient", DummyClient)

    ssh = SSHClient("host", 22, "user")
    ssh.connect()
    rc, out, err = ssh.exec_command("echo hi")
    assert rc == 0
    assert "connect" in calls


def test_ssh_client_fails_fast_with_default_retries(monkeypatch):
    connect_calls = []
    sleep_calls = []

    class FailingClient:
        def __init__(self, **kwargs):
            pass

        def load_system_host_keys(self):
            pass

        def set_missing_host_key_policy(self, policy):
            pass

        def connect(self, **kwargs):
            connect_calls.append("connect")
            raise ConnectionError("fail")

    monkeypatch.setattr("ulhpc_submit.ssh_client.paramiko.SSHClient", FailingClient)
    monkeypatch.setattr("ulhpc_submit.ssh_client.time.sleep", sleep_calls.append)

    ssh = SSHClient("host", 22, "user")  # default max_retries=1
    with pytest.raises(SyncNetworkError):
        ssh.connect()

    assert len(connect_calls) == 1
    assert len(sleep_calls) == 0


def test_ssh_client_retries_and_raises(monkeypatch):
    class FailingClient:
        def __init__(self, **kwargs):
            pass

        def load_system_host_keys(self):
            pass

        def set_missing_host_key_policy(self, policy):
            pass

        def connect(self, **kwargs):
            raise ConnectionError("fail")

    monkeypatch.setattr("ulhpc_submit.ssh_client.paramiko.SSHClient", FailingClient)

    ssh = SSHClient("host", 22, "user", max_retries=2)
    with pytest.raises(SyncNetworkError):
        ssh.connect()


def test_ssh_client_backoff_is_bounded(monkeypatch):
    connect_calls = []
    sleep_calls = []

    class FailingClient:
        def __init__(self, **kwargs):
            pass

        def load_system_host_keys(self):
            pass

        def set_missing_host_key_policy(self, policy):
            pass

        def connect(self, **kwargs):
            connect_calls.append("connect")
            raise ConnectionError("fail")

    monkeypatch.setattr("ulhpc_submit.ssh_client.paramiko.SSHClient", FailingClient)
    monkeypatch.setattr("ulhpc_submit.ssh_client.random.uniform", lambda _a, _b: 0.5)
    monkeypatch.setattr("ulhpc_submit.ssh_client.time.sleep", sleep_calls.append)

    ssh = SSHClient("host", 22, "user", max_retries=3)
    with pytest.raises(SyncNetworkError):
        ssh.connect()

    assert len(connect_calls) == 3
    assert len(sleep_calls) == 2
    # attempt 1 -> sleep ~2.5s, attempt 2 -> sleep ~4.5s
    assert 2.0 < sleep_calls[0] < 3.0
    assert 4.0 < sleep_calls[1] < 5.0


def test_ssh_client_backoff_caps_at_thirty_seconds(monkeypatch):
    sleep_calls = []

    class FailingClient:
        def __init__(self, **kwargs):
            pass

        def load_system_host_keys(self):
            pass

        def set_missing_host_key_policy(self, policy):
            pass

        def connect(self, **kwargs):
            raise ConnectionError("fail")

    monkeypatch.setattr("ulhpc_submit.ssh_client.paramiko.SSHClient", FailingClient)
    monkeypatch.setattr("ulhpc_submit.ssh_client.random.uniform", lambda _a, _b: 0.5)
    monkeypatch.setattr("ulhpc_submit.ssh_client.time.sleep", sleep_calls.append)

    ssh = SSHClient("host", 22, "user", max_retries=6)
    with pytest.raises(SyncNetworkError):
        ssh.connect()

    # With 6 attempts there are 5 sleeps; the last ones should be capped near 30.5s.
    assert all(s <= 30.5 for s in sleep_calls)


def test_ssh_client_error_warns_about_rate_limiting(monkeypatch):
    class FailingClient:
        def __init__(self, **kwargs):
            pass

        def load_system_host_keys(self):
            pass

        def set_missing_host_key_policy(self, policy):
            pass

        def connect(self, **kwargs):
            raise ConnectionError("fail")

    monkeypatch.setattr("ulhpc_submit.ssh_client.paramiko.SSHClient", FailingClient)

    ssh = SSHClient("host", 22, "user", max_retries=1)
    with pytest.raises(SyncNetworkError) as exc_info:
        ssh.connect()

    assert "rate-limiting" in SyncNetworkError.suggestion
    assert "rate-limiting" in str(exc_info.value)
