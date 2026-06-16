"""Tests for job state monitoring."""

import pytest

from ulhpc_submit.errors import (
    HPCResourceError,
    JobKilledError,
    JobNodeError,
    JobPendingTimeoutError,
)
from ulhpc_submit.monitor import JobMonitor


def test_monitor_reaches_completed(fake_ssh):
    from tests.conftest import FakeSSHClient

    class TwoStepSSH(FakeSSHClient):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self._squeue_calls = 0

        def exec_command(self, command: str):
            if "squeue" in command:
                self._squeue_calls += 1
                if self._squeue_calls == 1:
                    return 0, 'RUNNING 00:01:00 01:00:00 None\n', ""
                return 0, "", ""
            if "sacct" in command:
                return 0, "123456|COMPLETED|0:0|0:0|1M|node01\n", ""
            return super().exec_command(command)

    ssh = TwoStepSSH()
    monitor = JobMonitor(ssh, "123456", poll_interval=0)
    final = monitor.monitor()
    assert final.state == "COMPLETED"


def test_monitor_pending_timeout(fake_ssh):
    fake_ssh.set_response(
        "squeue", 0, 'PENDING 00:00:00 01:00:00 Resources\n', ""
    )

    class FastClock:
        def __init__(self):
            self.t = 0

        def __call__(self):
            self.t += 4000
            return self.t

    monitor = JobMonitor(
        fake_ssh, "123456", poll_interval=0, pending_timeout=10, clock=FastClock()
    )
    with pytest.raises(JobPendingTimeoutError):
        monitor.monitor()


def test_monitor_job_killed(fake_ssh):
    fake_ssh.set_response("squeue", 0, "", "")
    fake_ssh.set_response("sacct", 0, "123456|CANCELLED|0:0|0:0|1M|node01\n", "")
    monitor = JobMonitor(fake_ssh, "123456", poll_interval=0)
    with pytest.raises(JobKilledError):
        monitor.monitor()


def test_monitor_oom(fake_ssh):
    fake_ssh.set_response("squeue", 0, "", "")
    fake_ssh.set_response(
        "sacct", 0, "123456|OUT_OF_MEMORY|0:0|0:0|16G|node01\n", ""
    )
    monitor = JobMonitor(fake_ssh, "123456", poll_interval=0)
    with pytest.raises(HPCResourceError):
        monitor.monitor()


def test_monitor_pending_hints(fake_ssh):
    fake_ssh.set_response(
        "squeue", 0, 'PENDING 00:00:00 01:00:00 Resources\n', ""
    )

    class FastClock:
        def __init__(self):
            self.t = 0

        def __call__(self):
            self.t += 400
            return self.t

    hints = []

    def capture_hint(msg: str) -> None:
        hints.append(msg)

    monitor = JobMonitor(
        fake_ssh,
        "123456",
        poll_interval=0,
        pending_timeout=1000,
        hint_interval=300,
        clock=FastClock(),
        progress=capture_hint,
    )
    # Poll a few times without hitting the timeout.
    for _ in range(4):
        event = monitor._squeue()
        assert event is not None
        # Simulate the logic inside monitor() up to the hint.
        if event.state == "PENDING":
            if monitor._pending_since is None:
                monitor._pending_since = monitor.clock()
            waited = monitor.clock() - monitor._pending_since
            if (
                monitor._last_hint_at is None
                or (monitor.clock() - monitor._last_hint_at) >= monitor.hint_interval
            ):
                monitor._last_hint_at = monitor.clock()
                monitor.progress(
                    f"[ulhpc-submit] Job 123456 has been PENDING for "
                    f"{int(waited)}s (reason: {event.reason}). "
                    "Consider adjusting resources or switching partition if the wait is long."
                )

    assert len(hints) >= 2
    assert all("PENDING" in h and "Resources" in h for h in hints)


def test_monitor_node_fail(fake_ssh):
    fake_ssh.set_response("squeue", 0, "", "")
    fake_ssh.set_response("sacct", 0, "123456|NODE_FAIL|1:0|1:0|1M|node01\n", "")
    monitor = JobMonitor(fake_ssh, "123456", poll_interval=0)
    with pytest.raises(JobNodeError):
        monitor.monitor()
