"""Tests for log fetching and error classification."""

from pathlib import Path

from ulhpc_submit.errors import CodeError, EnvDependencyError, HPCResourceError, NetworkError
from ulhpc_submit.logs import LogManager, RunLogger, create_run_logger


def test_create_run_logger(tmp_path: Path):
    logger = create_run_logger(tmp_path, "foo")
    assert logger.run_dir.exists()
    assert (logger.run_dir / "run.log").exists()


def test_log_manager_fetch_and_merge(fake_ssh, tmp_path: Path):
    logger = RunLogger(tmp_path / "run")
    fake_ssh.files["/remote/job_42.out"] = "stdout line\n"
    fake_ssh.files["/remote/job_42.err"] = "stderr line\n"
    lm = LogManager(fake_ssh, "/remote", "42", logger)
    out, err = lm.fetch()
    assert "stdout line" in out
    assert "stderr line" in err
    lm.merge(out, err)
    log_text = logger.log_file.read_text(encoding="utf-8")
    assert "stdout line" in log_text


def test_log_manager_quotes_tail_paths(fake_ssh, tmp_path: Path):
    logger = RunLogger(tmp_path / "run")
    lm = LogManager(fake_ssh, "/remote dir", "42", logger)
    lm.fetch()
    commands = "\n".join(fake_ssh.commands)
    assert "tail -n 500 '/remote dir/job_42.out'" in commands
    assert "tail -n 500 '/remote dir/job_42.err'" in commands


def test_classify_traceback():
    logger = RunLogger(Path("/tmp/ulhpc_test_run"))
    lm = LogManager(None, "/remote", "1", logger)
    out = "Traceback (most recent call last):\n  File main.py line 5\nValueError: bad\n"
    err = ""
    error = lm.classify_output_errors(out, err)
    assert isinstance(error, CodeError)


def test_classify_missing_package():
    logger = RunLogger(Path("/tmp/ulhpc_test_run"))
    lm = LogManager(None, "/remote", "1", logger)
    err = "ModuleNotFoundError: No module named 'torch'\n"
    error = lm.classify_output_errors("", err)
    assert isinstance(error, EnvDependencyError)


def test_classify_network_error():
    logger = RunLogger(Path("/tmp/ulhpc_test_run"))
    lm = LogManager(None, "/remote", "1", logger)
    err = "requests.exceptions.ConnectionError: Connection refused\n"
    error = lm.classify_output_errors("", err)
    assert isinstance(error, NetworkError)


def test_classify_resource_error():
    logger = RunLogger(Path("/tmp/ulhpc_test_run"))
    lm = LogManager(None, "/remote", "1", logger)
    err = "slurmstepd: error: *** JOB 1 ON node01 CANCELLED DUE TO MEMORY LIMIT ***\n"
    error = lm.classify_output_errors("", err)
    assert isinstance(error, HPCResourceError)
