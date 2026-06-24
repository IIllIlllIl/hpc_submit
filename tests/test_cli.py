"""Tests for CLI argument parsing."""

import pytest

from ulhpc_submit import __version__
from ulhpc_submit.cli import main, parse_args
from ulhpc_submit.errors import SyncNetworkError


def test_parse_args_basic():
    args = parse_args(["python", "main.py"])
    assert args.command == ["python", "main.py"]


def test_parse_args_with_options():
    args = parse_args([
        "--partition", "gpu",
        "--gpus", "1",
        "--time", "02:00:00",
        "--", "python", "train.py",
    ])
    assert args.partition == "gpu"
    assert args.gpus == 1
    assert args.time == "02:00:00"
    assert args.command == ["python", "train.py"]


def test_parse_args_submit_only():
    args = parse_args(["--submit-only", "python", "main.py"])
    assert args.submit_only is True
    assert args.command == ["python", "main.py"]


def test_parse_args_detach_alias():
    args = parse_args(["--detach", "python", "main.py"])
    assert args.submit_only is True
    assert args.command == ["python", "main.py"]


def test_parse_args_runtime_options():
    args = parse_args([
        "--module", "lang/Python/3.11",
        "--module", "tools/Apptainer",
        "--python", "python3",
        "--no-conda",
        "python", "main.py",
    ])
    assert args.runtime_modules == ["lang/Python/3.11", "tools/Apptainer"]
    assert args.python == "python3"
    assert args.no_conda is True
    assert args.command == ["python", "main.py"]


def test_parse_args_stage_data_options():
    args = parse_args([
        "--stage-data", "output/dataset:~/hpc_datasets/project/dataset",
        "--link-as", "output/dataset",
        "python", "main.py",
    ])
    assert args.stage_data == ["output/dataset:~/hpc_datasets/project/dataset"]
    assert args.link_as == ["output/dataset"]
    assert args.command == ["python", "main.py"]


def test_parse_args_persistent_output_options():
    args = parse_args([
        "--persistent-output", "output/run:~/hpc_run_state/project/run",
        "python", "main.py",
    ])
    assert args.persistent_output == ["output/run:~/hpc_run_state/project/run"]
    assert args.command == ["python", "main.py"]


def test_parse_args_no_command():
    with pytest.raises(SystemExit):
        parse_args([])


def test_parse_args_allows_init_config_without_command():
    args = parse_args(["--init-config"])
    assert args.init_config is True
    assert args.command == []


def test_parse_args_allows_show_config_without_command():
    args = parse_args(["--show-config"])
    assert args.show_config is True
    assert args.command == []


def test_cli_version(capsys):
    with pytest.raises(SystemExit) as exc_info:
        main(["--version"])
    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert __version__ in captured.out


def test_cli_show_config(capsys, monkeypatch, tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("user: showuser\nhost: showhost\n", encoding="utf-8")

    rc = main(["--config", str(config_path), "--show-config"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "showuser" in captured.out
    assert "showhost" in captured.out


def test_cli_init_config(monkeypatch, tmp_path, capsys):
    config_path = tmp_path / "config.yaml"
    inputs = iter(["inituser", "", "", "", "", ""])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(inputs))

    rc = main(["--init-config", "--config", str(config_path)])
    assert rc == 0
    assert config_path.exists()
    captured = capsys.readouterr()
    assert "Config written" in captured.out


def test_cli_dry_run(project_dir, tmp_path, monkeypatch, capsys):
    # Patch main pipeline to avoid real SSH.
    from ulhpc_submit import main as main_module

    calls = []

    class FakePipeline:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            calls.append(kwargs)

        def run(self):
            return 0

    monkeypatch.setattr(main_module, "SubmissionPipeline", FakePipeline)

    rc = main([
        "--local-dir", str(project_dir),
        "--user", "testuser",
        "--dry-run",
        "python", "main.py",
    ])
    assert rc == 0
    assert calls
    assert calls[0]["dry_run"] is True


def test_cli_submit_only_passed_to_pipeline(project_dir, monkeypatch):
    from ulhpc_submit import main as main_module

    calls = []

    class FakePipeline:
        def __init__(self, **kwargs):
            calls.append(kwargs)

        def run(self):
            return 0

    monkeypatch.setattr(main_module, "SubmissionPipeline", FakePipeline)

    rc = main([
        "--local-dir", str(project_dir),
        "--user", "testuser",
        "--submit-only",
        "python", "main.py",
    ])
    assert rc == 0
    assert calls
    assert calls[0]["submit_only"] is True


def test_cli_runtime_options_passed_to_pipeline(project_dir, monkeypatch):
    from ulhpc_submit import main as main_module

    calls = []

    class FakePipeline:
        def __init__(self, **kwargs):
            calls.append(kwargs)

        def run(self):
            return 0

    monkeypatch.setattr(main_module, "SubmissionPipeline", FakePipeline)

    rc = main([
        "--local-dir", str(project_dir),
        "--user", "testuser",
        "--module", "lang/Python/3.11",
        "--python", "python3",
        "--no-conda",
        "python", "main.py",
    ])
    assert rc == 0
    assert calls[0]["runtime_modules"] == ["lang/Python/3.11"]
    assert calls[0]["python_executable"] == "python3"
    assert calls[0]["use_conda"] is False


def test_cli_stage_data_passed_to_pipeline(project_dir, monkeypatch):
    from ulhpc_submit import main as main_module

    calls = []

    class FakePipeline:
        def __init__(self, **kwargs):
            calls.append(kwargs)

        def run(self):
            return 0

    monkeypatch.setattr(main_module, "SubmissionPipeline", FakePipeline)

    rc = main([
        "--local-dir", str(project_dir),
        "--user", "testuser",
        "--stage-data", "output/dataset:~/hpc_datasets/project/dataset",
        "--link-as", "output/dataset",
        "python", "main.py",
    ])
    assert rc == 0
    assert calls[0]["stage_data"] == ["output/dataset:~/hpc_datasets/project/dataset"]
    assert calls[0]["link_as"] == ["output/dataset"]


def test_cli_persistent_output_passed_to_pipeline(project_dir, monkeypatch):
    from ulhpc_submit import main as main_module

    calls = []

    class FakePipeline:
        def __init__(self, **kwargs):
            calls.append(kwargs)

        def run(self):
            return 0

    monkeypatch.setattr(main_module, "SubmissionPipeline", FakePipeline)

    rc = main([
        "--local-dir", str(project_dir),
        "--user", "testuser",
        "--persistent-output", "output/run:~/hpc_run_state/project/run",
        "python", "main.py",
    ])
    assert rc == 0
    assert calls[0]["persistent_output"] == ["output/run:~/hpc_run_state/project/run"]


def test_cli_rejects_placeholder_user(capsys, monkeypatch, tmp_path):
    # Avoid creating real config at default path.
    monkeypatch.setenv("HOME", str(tmp_path))

    rc = main([
        "--local-dir", str(tmp_path),
        "python", "main.py",
    ])
    assert rc == 2
    captured = capsys.readouterr()
    assert "CONFIG_ERROR" in captured.err
    assert "--init-config" in captured.err


def test_parse_args_allows_test_connection_without_command():
    args = parse_args(["--test-connection"])
    assert args.test_connection is True
    assert args.command == []


def test_cli_test_connection_success(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    connect_calls = []
    exec_calls = []

    class DummySSHClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def connect(self):
            connect_calls.append(self.kwargs.get("max_retries"))

        def exec_command(self, command):
            exec_calls.append(command)
            return 0, "", ""

        def close(self):
            pass

    monkeypatch.setattr("ulhpc_submit.cli.SSHClient", DummySSHClient)

    rc = main(["--test-connection", "--user", "testuser"])
    assert rc == 0
    assert connect_calls == [1]
    assert exec_calls == ["true"]
    captured = capsys.readouterr()
    assert "SSH connectivity test succeeded" in captured.out


def test_cli_test_connection_failure(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))

    class FailingSSHClient:
        def __init__(self, **kwargs):
            pass

        def connect(self):
            raise SyncNetworkError("forced failure")

    monkeypatch.setattr("ulhpc_submit.cli.SSHClient", FailingSSHClient)

    rc = main(["--test-connection", "--user", "testuser"])
    assert rc == 2
    captured = capsys.readouterr()
    assert "rate-limiting" in captured.err


def test_cli_test_connection_forces_single_attempt_despite_config(monkeypatch, tmp_path, capsys):
    """--test-connection must always try only once, even if config sets a higher value."""
    monkeypatch.setenv("HOME", str(tmp_path))
    config_path = tmp_path / "config.yaml"
    config_path.write_text("user: testuser\nmax_ssh_retries: 5\n", encoding="utf-8")
    connect_calls = []

    class DummySSHClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def connect(self):
            connect_calls.append(self.kwargs.get("max_retries"))

        def exec_command(self, command):
            return 0, "", ""

        def close(self):
            pass

    monkeypatch.setattr("ulhpc_submit.cli.SSHClient", DummySSHClient)

    rc = main(["--config", str(config_path), "--test-connection"])
    assert rc == 0
    assert connect_calls == [1]


def test_cli_test_connection_validates_config(monkeypatch, tmp_path, capsys):
    """Placeholder user should be rejected before any SSH attempt."""
    monkeypatch.setenv("HOME", str(tmp_path))
    rc = main(["--test-connection"])
    assert rc == 2
    captured = capsys.readouterr()
    assert "CONFIG_ERROR" in captured.err


def test_cli_max_ssh_retries_merged_into_config(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    from ulhpc_submit import main as main_module

    calls = []

    class SpyPipeline:
        def __init__(self, config, **kwargs):
            calls.append(config)

        def run(self):
            return 0

    monkeypatch.setattr(main_module, "SubmissionPipeline", SpyPipeline)

    rc = main([
        "--local-dir", str(tmp_path),
        "--user", "testuser",
        "--max-ssh-retries", "3",
        "python", "main.py",
    ])
    assert rc == 0
    assert len(calls) == 1
    assert calls[0].max_ssh_retries == 3


def test_cli_sync_free_space_margin_merged_into_config(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    from ulhpc_submit import main as main_module

    calls = []

    class SpyPipeline:
        def __init__(self, config, **kwargs):
            calls.append(config)

        def run(self):
            return 0

    monkeypatch.setattr(main_module, "SubmissionPipeline", SpyPipeline)

    rc = main([
        "--local-dir", str(tmp_path),
        "--user", "testuser",
        "--sync-free-space-margin", "2.0",
        "python", "main.py",
    ])
    assert rc == 0
    assert len(calls) == 1
    assert calls[0].sync_free_space_margin == 2.0
