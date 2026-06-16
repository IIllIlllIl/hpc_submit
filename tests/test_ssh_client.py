"""Tests for SSH client wrapper."""

import pytest

from ulhpc_submit.errors import SyncNetworkError
from ulhpc_submit.ssh_client import SSHClient


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
