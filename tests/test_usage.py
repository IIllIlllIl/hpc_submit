"""Tests for FairShare and Slurm usage summaries."""

from ulhpc_submit.usage import (
    build_fairshare_command,
    build_sacct_recent_command,
    extract_fairshare,
    parse_sacct_jobs,
    parse_slurm_duration,
    summarize_usage,
    UsageSummary,
)


def test_parse_slurm_duration():
    assert parse_slurm_duration("01:02:03") == 3723
    assert parse_slurm_duration("00:45.455") == 45.455
    assert parse_slurm_duration("2-01:00:00") == 176400
    assert parse_slurm_duration("04:05") == 245
    assert parse_slurm_duration("Unknown") == 0


def test_parse_sacct_jobs_merges_batch_max_rss_and_skips_step_records():
    jobs = parse_sacct_jobs(
        "123|train|batch|COMPLETED|00:00:46|1|00:45.455|4G|\n"
        "123.batch|batch||COMPLETED|00:00:46|1|00:45.455||539880K\n"
        "123.extern|extern||COMPLETED|00:00:47|1|00:00:00||\n"
        "124|eval|gpu|FAILED|00:30:00|2|00:05:00|16G|4G\n"
    )

    assert [job.job_id for job in jobs] == ["123", "124"]
    assert jobs[0].max_rss == "539880K"
    assert round(jobs[0].cpu_efficiency or 0, 3) == 0.988


def test_extract_fairshare_from_ulhpcshare_table():
    lines, value = extract_fairshare(
        "Account User RawShares NormShares EffectvUsage FairShare\n"
        "abc testuser 1 0.5 0.2 0.812345\n"
    )

    assert value == "0.812345"
    assert lines[0].startswith("Account User")


def test_summarize_usage_adds_behavior_hints():
    jobs = parse_sacct_jobs(
        "123|train|batch|COMPLETED|01:00:00|4|00:30:00|8G|2G\n"
        "124|eval|gpu|FAILED|00:30:00|2|00:05:00|16G|4G\n"
    )
    text = summarize_usage(
        UsageSummary(
            user="testuser",
            days=30,
            jobs=jobs,
            fairshare_lines=[],
            fairshare_value="0.42",
        )
    )

    assert "Recent jobs: 2" in text
    assert "allocated core-hours: 5.00" in text
    assert "Average CPU efficiency" in text
    assert "did not complete" in text
    assert "below 50%" in text


def test_usage_commands_quote_user():
    assert build_fairshare_command("bad user;touch x") == "ulhpcshare -u 'bad user;touch x'"
    command = build_sacct_recent_command("bad user;touch x", 7)
    assert "sacct -u 'bad user;touch x'" in command
    assert "--starttime=now-7days" in command
