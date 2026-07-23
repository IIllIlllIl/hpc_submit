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


def test_calibration_lines_handles_iris_fractional_cpu_and_batch_rss():
    job = parse_sacct_jobs(
        "5552552|pilot|batch|COMPLETED|00:00:46|1|00:45.455|4G|\n"
        "5552552.batch|batch||COMPLETED|00:00:46|1|00:45.455||539880K\n"
        "5552552.extern|extern||COMPLETED|00:00:47|1|00:00:00||\n"
    )[0]

    text = "\n".join(calibration_lines(job))

    assert "CPU efficiency: 98.8%" in text
    assert "Memory: requested 4G; max RSS 539880K" in text
    assert "Memory used/requested: 12.9%" in text
    assert "requesting fewer CPUs" not in text
    assert "memory request appears high" in text
