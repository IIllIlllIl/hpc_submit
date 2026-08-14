"""Local logging, remote output fetching, and log-based error classification."""

import json
import os
import re
import shlex
import shutil
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple
from uuid import uuid4

from .errors import CodeError, EnvDependencyError, HPCResourceError, NetworkError, ULHPCError
from .ssh_client import SSHClient
from .remote_paths import stderr_path as managed_stderr_path
from .remote_paths import stdout_path as managed_stdout_path


class RunLogger:
    """Timestamped local logger for a single run."""

    def __init__(self, run_dir: Path):
        self.run_dir = run_dir
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.log_file = run_dir / "run.log"
        self.events_file = run_dir / "events.jsonl"
        # Ensure files exist so tests and users can inspect them immediately.
        self.log_file.touch(exist_ok=True)
        self.events_file.touch(exist_ok=True)

    def _ts(self) -> str:
        return datetime.now().isoformat(timespec="seconds")

    def info(self, message: str) -> None:
        line = f"[{self._ts()}] INFO {message}"
        self._write(line)

    def warning(self, message: str) -> None:
        line = f"[{self._ts()}] WARNING {message}"
        self._write(line)

    def error(self, message: str) -> None:
        line = f"[{self._ts()}] ERROR {message}"
        self._write(line)

    def event(self, category: str, state: str, detail: str = "") -> None:
        record = {
            "timestamp": self._ts(),
            "category": category,
            "state": state,
            "detail": detail,
        }
        with self.events_file.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        self.info(f"[{category}] {state} {detail}".strip())

    def write_manifest(self, data: dict) -> Path:
        """Write a structured job metadata manifest for wrapper tooling."""
        path = self.run_dir / "manifest.json"
        with path.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
            fh.write("\n")
        self.info(f"Manifest written: {path}")
        return path

    def _write(self, line: str) -> None:
        with self.log_file.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")


class LogManager:
    """Fetch and merge remote job output with the local log."""

    def __init__(
        self,
        ssh: SSHClient,
        remote_dir: str,
        job_id: str,
        run_logger: RunLogger,
        full_logs: bool = False,
        tail_lines: int = 500,
        stdout_path: Optional[str] = None,
        stderr_path: Optional[str] = None,
    ):
        self.ssh = ssh
        self.remote_dir = remote_dir
        self.job_id = job_id
        self.run_logger = run_logger
        self.full_logs = full_logs
        self.tail_lines = tail_lines
        self.stdout_path = stdout_path
        self.stderr_path = stderr_path
        self.fetch_errors: List[str] = []

    def _remote_out_path(self) -> str:
        return self.stdout_path or managed_stdout_path(self.remote_dir, self.job_id)

    def _remote_err_path(self) -> str:
        return self.stderr_path or managed_stderr_path(self.remote_dir, self.job_id)

    def _candidate_paths(self, label: str) -> List[str]:
        primary = self._remote_out_path() if label == "out" else self._remote_err_path()
        legacy = f"{self.remote_dir}/job_{self.job_id}.{label}"
        return [primary] if primary == legacy else [primary, legacy]

    def fetch(self) -> Tuple[str, str]:
        """Return (stdout, stderr) strings.

        Falls back to tailing over SSH if files are large or SFTP fails.
        """
        self.fetch_errors = []
        stdout = self._fetch_file("out")
        stderr = self._fetch_file("err")
        return stdout, stderr

    def _fetch_file(self, label: str) -> str:
        errors: List[str] = []
        for remote_path in self._candidate_paths(label):
            if self.full_logs:
                local_tmp = self.run_logger.run_dir / f"job_{self.job_id}.{label}"
                try:
                    self.ssh.sftp_get(remote_path, str(local_tmp))
                    return local_tmp.read_text(encoding="utf-8", errors="replace")
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{remote_path}: {exc}")
            cmd = f"tail -n {self.tail_lines} {shlex.quote(remote_path)}"
            rc, out, err = self.ssh.exec_command(cmd)
            if rc == 0:
                return out
            errors.append(f"{remote_path}: {(err or out).strip()}")
        detail = f"Could not fetch remote {label}: " + "; ".join(errors)
        self.fetch_errors.append(detail)
        self.run_logger.error(detail)
        return ""

    def merge(self, stdout: str, stderr: str) -> None:
        """Append remote outputs to local log file."""
        self.run_logger.info("=== remote stdout ===")
        for line in stdout.splitlines():
            self._write_line(f"  {line}")
        self.run_logger.info("=== remote stderr ===")
        for line in stderr.splitlines():
            self._write_line(f"  {line}")

    def _write_line(self, line: str) -> None:
        with self.run_logger.log_file.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def classify_output_errors(self, stdout: str, stderr: str) -> Optional[ULHPCError]:
        """Scan merged output and return a typed error if a known pattern matches."""
        combined = f"{stdout}\n{stderr}"
        lower = combined.lower()

        # HPC resource failures detected by Slurm step messages.
        if any(
            pattern in lower
            for pattern in (
                "slurmstepd: error:",
                "out-of-memory",
                "oom-killer",
                "killed process",
                "exceeded memory",
                "disk quota exceeded",
            )
        ):
            return HPCResourceError(
                "Resource limit detected in job output (OOM/disk/quota)."
            )

        # Network/API failures inside user code.
        if any(
            pattern in lower
            for pattern in (
                "connectionerror",
                "connection refused",
                "timeouterror",
                "urlerror",
                "temporary failure in name resolution",
                "no route to host",
            )
        ):
            return NetworkError(
                "External network/API call failed inside the job."
            )

        # Missing package.
        if any(
            pattern in lower
            for pattern in (
                "modulenotfounderror",
                "no module named",
                "package not found",
                "could not find a version that satisfies",
            )
        ):
            return EnvDependencyError(
                "Missing or incompatible Python package detected in job output."
            )

        # Python traceback / syntax error.
        if "traceback" in lower or "syntaxerror" in lower or "indentationerror" in lower:
            # Extract last few traceback lines for the message.
            snippet = self._extract_traceback(combined)
            return CodeError(f"User code raised an exception.\n{snippet}")

        return None

    def _extract_traceback(self, text: str) -> str:
        lines = text.splitlines()
        # Find last "Traceback" line and capture until end.
        start = -1
        for i in range(len(lines) - 1, -1, -1):
            if "traceback" in lines[i].lower():
                start = i
                break
        if start >= 0:
            snippet = "\n".join(lines[start : start + 20])
            return snippet
        return text[-500:]


def create_run_logger(config_log_dir: Path, project_name: str) -> RunLogger:
    """Create a logger in a timestamped run directory."""
    now = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = config_log_dir / f"{project_name}_{now}_{uuid4().hex[:8]}"
    return RunLogger(run_dir)
