"""Tests for CLI argument parsing."""

import pytest

from ulhpc_submit import __version__
from ulhpc_submit.cli import main, parse_args


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


def test_cli_warns_on_missing_config(capsys, monkeypatch, tmp_path):
    # Provide a valid user via CLI so validation passes; only check the warning.
    monkeypatch.setenv("HOME", str(tmp_path))

    from ulhpc_submit import main as main_module
    monkeypatch.setattr(main_module.SubmissionPipeline, "run", lambda self: 0)

    rc = main([
        "--local-dir", str(tmp_path),
        "--user", "testuser",
        "python", "main.py",
    ])
    assert rc == 0
    captured = capsys.readouterr()
    assert "No config file found" in captured.err
