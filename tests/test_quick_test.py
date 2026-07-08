"""Tests for quick-test calibration summaries."""

from ulhpc_submit.quick_test import calibration_lines, parse_memory_to_mib
from ulhpc_submit.usage import parse_sacct_jobs


def test_parse_memory_to_mib():
    assert parse_memory_to_mib("1024K") == 1
    assert parse_memory_to_mib("512M") == 512
    assert parse_memory_to_mib("4G") == 4096
    assert parse_memory_to_mib("4Gn") == 4096
    assert parse_memory_to_mib("unknown-value") is None


def test_calibration_lines_add_cpu_and_memory_hints():
    job = parse_sacct_jobs(
        "123|train|batch|COMPLETED|00:10:00|8|00:20:00|16G|2G\n"
    )[0]

    lines = calibration_lines(
        job,
        cpu_efficiency_threshold=0.5,
        memory_headroom_threshold=0.25,
    )
    text = "\n".join(lines)

    assert "CPU efficiency: 25.0%" in text
    assert "Allocated core-hours: 1.33" in text
    assert "requesting fewer CPUs" in text
    assert "Memory used/requested: 12.5%" in text
    assert "memory request appears high" in text


def test_calibration_lines_flags_oom():
    job = parse_sacct_jobs(
        "123|train|batch|OUT_OF_MEMORY|00:10:00|4|00:40:00|8G|8G\n"
    )[0]

    text = "\n".join(calibration_lines(job))

    assert "state: OUT_OF_MEMORY" in text
    assert "increase --mem" in text
