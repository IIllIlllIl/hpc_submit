"""Job state monitoring via squeue/sacct."""

import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional

from .errors import (
    HPCResourceError,
    JobKilledError,
    JobNodeError,
    JobPendingTimeoutError,
)
from .ssh_client import SSHClient


@dataclass
class JobStateEvent:
    timestamp: str
    state: str
    info: str
    elapsed: str = ""
    reason: str = ""


class JobMonitor:
    """Poll Slurm until a job reaches a terminal state."""

    TERMINAL_STATES = {"COMPLETED", "FAILED", "CANCELLED", "TIMEOUT", "NODE_FAIL", "OUT_OF_MEMORY"}
    RESOURCE_FAILURE_STATES = {"TIMEOUT", "OUT_OF_MEMORY", "NODE_FAIL"}

    def __init__(
        self,
        ssh: SSHClient,
        job_id: str,
        poll_interval: int = 10,
        pending_timeout: int = 3600,
        hint_interval: int = 300,
        clock: Optional[Callable[[], float]] = None,
        progress: Optional[Callable[[str], None]] = None,
    ):
        self.ssh = ssh
        self.job_id = job_id
        self.poll_interval = poll_interval
        self.pending_timeout = pending_timeout
        self.hint_interval = hint_interval
        self.clock = clock or time.time
        self.progress = progress or (lambda _msg: None)
        self.events: list = []
        self._pending_since: Optional[float] = None
        self._last_hint_at: Optional[float] = None

    def _now(self) -> str:
        return datetime.now().isoformat(timespec="seconds")

    def _squeue(self) -> Optional[JobStateEvent]:
        """Query squeue. Returns current event or None if job no longer queued/running."""
        fmt = '"%T %M %L %R"'
        cmd = f"squeue -j {self.job_id} -h -o {fmt}"
        rc, out, err = self.ssh.exec_command(cmd)
        if rc != 0 or not out.strip():
            return None
        # Output: "STATE ELAPSED TIMELIMIT REASON"
        parts = out.strip().split(None, 3)
        if len(parts) < 4:
            return None
        state, elapsed, time_limit, reason = parts
        state = state.strip('"')
        reason = reason.strip('"')
        return JobStateEvent(
            timestamp=self._now(),
            state=state,
            elapsed=elapsed,
            reason=reason,
            info="squeue",
        )

    def _sacct(self) -> JobStateEvent:
        """Query sacct for final accounting information."""
        fmt = "JobID,State,ExitCode,DerivedExitCode,MaxRSS,NodeList"
        cmd = f'sacct -j {self.job_id} --format={fmt} -n -P'
        rc, out, err = self.ssh.exec_command(cmd)
        state = "UNKNOWN"
        exit_code = "0:0"
        max_rss = ""
        node_list = ""
        if rc == 0 and out.strip():
            # First non-.batch line
            for line in out.strip().splitlines():
                if ".batch" in line or ".extern" in line:
                    continue
                cols = line.strip().split("|")
                if len(cols) >= 2:
                    state = cols[1]
                    exit_code = cols[2] if len(cols) > 2 else "0:0"
                    max_rss = cols[4] if len(cols) > 4 else ""
                    node_list = cols[5] if len(cols) > 5 else ""
                    break
        return JobStateEvent(
            timestamp=self._now(),
            state=state,
            info=f"exit={exit_code} rss={max_rss} nodes={node_list}",
        )

    def _record(self, event: JobStateEvent) -> None:
        if not self.events or self.events[-1].state != event.state:
            self.events.append(event)

    def monitor(self) -> JobStateEvent:
        """Poll until terminal state, then return final sacct event.

        Raises:
            JobPendingTimeoutError: if the job stays PENDING too long.
            JobKilledError: if the job is cancelled or times out.
            JobNodeError: if a node failure occurs.
            HPCResourceError: if the job fails due to OOM/resource limits.
        """
        start = self.clock()
        while True:
            event = self._squeue()
            if event is None:
                # Job left the queue; get final accounting data.
                final = self._sacct()
                self._record(final)
                self._raise_if_failure(final)
                return final

            self._record(event)

            if event.state == "PENDING":
                if self._pending_since is None:
                    self._pending_since = self.clock()
                waited = self.clock() - self._pending_since
                if waited >= self.pending_timeout:
                    raise JobPendingTimeoutError(
                        f"Job {self.job_id} has been pending for {int(waited)}s "
                        f"(limit {self.pending_timeout}s). Reason: {event.reason}"
                    )
                if (
                    self._last_hint_at is None
                    or (self.clock() - self._last_hint_at) >= self.hint_interval
                ):
                    self._last_hint_at = self.clock()
                    self.progress(
                        f"[ulhpc-submit] Job {self.job_id} has been PENDING for "
                        f"{int(waited)}s (reason: {event.reason}). "
                        "Consider adjusting resources or switching partition if the wait is long."
                    )
            else:
                self._pending_since = None
                self._last_hint_at = None

            elapsed_total = self.clock() - start
            # Safety break so a stuck monitor does not loop forever.
            if elapsed_total > 30 * 24 * 3600:
                raise JobKilledError(
                    f"Job {self.job_id} monitoring exceeded 30 days; aborting."
                )

            time.sleep(self.poll_interval)

    def _raise_if_failure(self, event: JobStateEvent) -> None:
        state = event.state
        if state in ("CANCELLED",):
            raise JobKilledError(f"Job {self.job_id} was cancelled: {event.info}")
        if state == "TIMEOUT":
            raise HPCResourceError(
                f"Job {self.job_id} hit wallclock limit: {event.info}"
            )
        if state == "OUT_OF_MEMORY":
            raise HPCResourceError(
                f"Job {self.job_id} ran out of memory: {event.info}"
            )
        if state == "NODE_FAIL":
            raise JobNodeError(f"Job {self.job_id} suffered a node failure: {event.info}")
        if state == "FAILED":
            # Only treat as a resource error when sacct explicitly signals OOM.
            # Generic FAILED jobs are diagnosed from stdout/stderr by LogManager.
            info_lower = event.info.lower()
            if any(k in info_lower for k in ("oom", "out-of-memory")):
                raise HPCResourceError(
                    f"Job {self.job_id} failed due to resource limits: {event.info}"
                )
