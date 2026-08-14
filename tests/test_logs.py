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


def test_log_manager_prefers_managed_logs_and_accepts_empty_files(fake_ssh, tmp_path: Path):
    logger = RunLogger(tmp_path / "run")
    fake_ssh.files["/remote/.ulhpc_submit/logs/job_42.out"] = ""
    fake_ssh.files["/remote/.ulhpc_submit/logs/job_42.err"] = "managed stderr\n"
    fake_ssh.files["/remote/job_42.out"] = "legacy stdout\n"
    lm = LogManager(fake_ssh, "/remote", "42", logger)

    out, err = lm.fetch()

    assert out == ""
    assert err == "managed stderr\n"
    assert lm.fetch_errors == []
    assert not any("/remote/job_42.out" in command for command in fake_ssh.commands)


def test_log_manager_reports_one_missing_stream_and_keeps_other(fake_ssh, tmp_path: Path):
    logger = RunLogger(tmp_path / "run")
    fake_ssh.files["/remote/.ulhpc_submit/logs/job_42.out"] = "available\n"
    lm = LogManager(fake_ssh, "/remote", "42", logger)

    out, err = lm.fetch()

    assert out == "available\n"
    assert err == ""
    assert len(lm.fetch_errors) == 1
    assert "job_42.err" in lm.fetch_errors[0]


def test_create_run_logger_is_unique_within_same_second(tmp_path: Path):
    first = create_run_logger(tmp_path, "project")
    second = create_run_logger(tmp_path, "project")
    assert first.run_dir != second.run_dir


def test_log_manager_quotes_tail_paths(fake_ssh, tmp_path: Path):
    logger = RunLogger(tmp_path / "run")
    lm = LogManager(fake_ssh, "/remote dir", "42", logger)
    lm.fetch()
    commands = "\n".join(fake_ssh.commands)
    assert "tail -n 500 '/remote dir/.ulhpc_submit/logs/job_42.out'" in commands
    assert "tail -n 500 '/remote dir/job_42.out'" in commands
    assert "tail -n 500 '/remote dir/.ulhpc_submit/logs/job_42.err'" in commands
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
