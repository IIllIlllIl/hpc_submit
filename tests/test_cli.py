"""Tests for CLI argument parsing."""

import pytest

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
        "--dry-run",
        "python", "main.py",
    ])
    assert rc == 0
    assert calls
    assert calls[0]["dry_run"] is True
