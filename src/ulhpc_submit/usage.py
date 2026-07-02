"""Fairshare and recent Slurm usage summaries."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import shlex
from typing import List, Optional, Tuple


SACCT_FIELDS = [
    "JobID",
    "JobName",
    "Partition",
    "State",
    "Elapsed",
    "AllocCPUS",
    "TotalCPU",
    "ReqMem",
    "MaxRSS",
]


@dataclass
class UsageJob:
    job_id: str
    job_name: str
    partition: str
    state: str
    elapsed: str
    alloc_cpus: int
    total_cpu: str
    req_mem: str
    max_rss: str

    @property
    def base_state(self) -> str:
        return self.state.split()[0].split("+", 1)[0]

    @property
    def elapsed_seconds(self) -> int:
        return parse_slurm_duration(self.elapsed)

    @property
    def total_cpu_seconds(self) -> int:
        return parse_slurm_duration(self.total_cpu)

    @property
    def core_hours(self) -> float:
        return (self.elapsed_seconds * max(self.alloc_cpus, 0)) / 3600

    @property
    def cpu_efficiency(self) -> Optional[float]:
        allocated = self.elapsed_seconds * self.alloc_cpus
        if allocated <= 0:
            return None
        return min(self.total_cpu_seconds / allocated, 1.0)


@dataclass
class UsageSummary:
    user: str
    days: int
    jobs: List[UsageJob]
    fairshare_lines: List[str]
    fairshare_value: Optional[str]
    fairshare_error: Optional[str] = None
    sacct_error: Optional[str] = None

    @property
    def state_counts(self) -> Counter:
        return Counter(job.base_state for job in self.jobs)

    @property
    def total_core_hours(self) -> float:
        return sum(job.core_hours for job in self.jobs)

    @property
    def cpu_efficiencies(self) -> List[float]:
        return [
            efficiency
            for job in self.jobs
            if (efficiency := job.cpu_efficiency) is not None
        ]


def parse_slurm_duration(value: str) -> int:
    """Parse Slurm durations like DD-HH:MM:SS, HH:MM:SS, or MM:SS."""
    text = value.strip()
    if not text or text in {"Unknown", "UNLIMITED", "None"}:
        return 0
    days = 0
    if "-" in text:
        day_text, text = text.split("-", 1)
        try:
            days = int(day_text)
        except ValueError:
            return 0
    parts = text.split(":")
    try:
        numbers = [int(part) for part in parts]
    except ValueError:
        return 0
    if len(numbers) == 3:
        hours, minutes, seconds = numbers
    elif len(numbers) == 2:
        hours = 0
        minutes, seconds = numbers
    elif len(numbers) == 1:
        hours = 0
        minutes = 0
        seconds = numbers[0]
    else:
        return 0
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def parse_sacct_jobs(output: str) -> List[UsageJob]:
    jobs: List[UsageJob] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        parts = line.rstrip("\n").split("|")
        if len(parts) < len(SACCT_FIELDS):
            continue
        job_id = parts[0].strip()
        if "." in job_id:
            continue
        try:
            alloc_cpus = int(parts[5].strip() or "0")
        except ValueError:
            alloc_cpus = 0
        jobs.append(
            UsageJob(
                job_id=job_id,
                job_name=parts[1].strip(),
                partition=parts[2].strip(),
                state=parts[3].strip(),
                elapsed=parts[4].strip(),
                alloc_cpus=alloc_cpus,
                total_cpu=parts[6].strip(),
                req_mem=parts[7].strip(),
                max_rss=parts[8].strip(),
            )
        )
    return jobs


def extract_fairshare(output: str) -> Tuple[List[str], Optional[str]]:
    """Return compact fairshare rows and the first FairShare value if present."""
    lines = [line.rstrip() for line in output.splitlines() if line.strip()]
    if not lines:
        return [], None
    header_index = next(
        (idx for idx, line in enumerate(lines) if "FairShare" in line.split()),
        None,
    )
    if header_index is None:
        return lines[:5], None
    header = lines[header_index].split()
    try:
        fairshare_index = header.index("FairShare")
    except ValueError:
        return lines[header_index : header_index + 5], None
    rows = lines[header_index + 1 : header_index + 6]
    value = None
    for row in rows:
        cols = row.split()
        if len(cols) > fairshare_index:
            value = cols[fairshare_index]
            break
    return [lines[header_index], *rows], value


def summarize_usage(summary: UsageSummary, job_id: Optional[str] = None) -> str:
    lines = [
        f"[ulhpc-submit] Slurm usage summary for {summary.user}",
    ]
    if job_id:
        lines.append(f"Job: {job_id}")
    else:
        lines.append(f"Window: last {summary.days} days")

    if summary.fairshare_error:
        lines.append(f"FairShare: unavailable ({summary.fairshare_error})")
    elif summary.fairshare_value:
        lines.append(
            f"FairShare: {summary.fairshare_value} "
            "(higher usually means better queue priority)"
        )
    elif summary.fairshare_lines:
        lines.append("FairShare: see remote output below")
    else:
        lines.append("FairShare: unavailable")

    if summary.sacct_error:
        lines.append(f"Accounting: unavailable ({summary.sacct_error})")
        return "\n".join(lines)

    if not summary.jobs:
        lines.append("Recent jobs: none found")
        return "\n".join(lines)

    states = ", ".join(
        f"{state.lower()}={count}" for state, count in summary.state_counts.most_common()
    )
    lines.append(
        f"Recent jobs: {len(summary.jobs)}; states: {states}; "
        f"allocated core-hours: {summary.total_core_hours:.2f}"
    )

    efficiencies = summary.cpu_efficiencies
    if efficiencies:
        average = sum(efficiencies) / len(efficiencies)
        lines.append(f"Average CPU efficiency: {average * 100:.1f}%")

    if job_id and len(summary.jobs) == 1:
        job = summary.jobs[0]
        lines.append(f"Memory: requested {job.req_mem or 'unknown'}; max RSS {job.max_rss or 'unknown'}")

    partition_counts = Counter(job.partition or "(none)" for job in summary.jobs)
    partitions = ", ".join(
        f"{partition}={count}" for partition, count in partition_counts.most_common(3)
    )
    lines.append(f"Top partitions: {partitions}")

    lines.extend(_usage_hints(summary))
    return "\n".join(lines)


def _usage_hints(summary: UsageSummary) -> List[str]:
    hints: List[str] = []
    states = summary.state_counts
    failed = sum(states[state] for state in ("FAILED", "TIMEOUT", "OUT_OF_MEMORY", "CANCELLED"))
    if failed:
        hints.append(
            f"Hint: {failed} recent job(s) did not complete; failed or oversized jobs still consume accounting usage."
        )
    efficiencies = summary.cpu_efficiencies
    if efficiencies and sum(efficiencies) / len(efficiencies) < 0.5:
        hints.append(
            "Hint: average CPU efficiency is below 50%; consider requesting fewer CPUs or improving parallelism."
        )
    if summary.total_core_hours > 0 and summary.fairshare_value:
        hints.append(
            "Hint: recent CPU/GPU/memory usage can lower FairShare, so smaller test jobs may queue sooner."
        )
    return hints


def build_fairshare_command(user: str) -> str:
    return f"ulhpcshare -u {shlex.quote(user)}"


def build_sacct_recent_command(user: str, days: int) -> str:
    fields = ",".join(SACCT_FIELDS)
    return (
        f"sacct -u {shlex.quote(user)} --starttime=now-{days}days "
        f"--format={fields} -P -n"
    )


def build_sacct_job_command(job_id: str) -> str:
    fields = ",".join(SACCT_FIELDS)
    return f"sacct -j {shlex.quote(job_id)} --format={fields} -P -n"


def collect_usage_summary(
    ssh,
    user: str,
    days: int,
    job_id: Optional[str] = None,
) -> UsageSummary:
    fairshare_lines: List[str] = []
    fairshare_value: Optional[str] = None
    fairshare_error: Optional[str] = None
    rc, out, err = ssh.exec_command(build_fairshare_command(user))
    if rc == 0:
        fairshare_lines, fairshare_value = extract_fairshare(out)
    else:
        fairshare_error = (err or out).strip() or "ulhpcshare failed"

    command = build_sacct_job_command(job_id) if job_id else build_sacct_recent_command(user, days)
    rc, out, err = ssh.exec_command(command)
    sacct_error = None
    jobs: List[UsageJob] = []
    if rc == 0:
        jobs = parse_sacct_jobs(out)
    else:
        sacct_error = (err or out).strip() or "sacct failed"

    return UsageSummary(
        user=user,
        days=days,
        jobs=jobs,
        fairshare_lines=fairshare_lines,
        fairshare_value=fairshare_value,
        fairshare_error=fairshare_error,
        sacct_error=sacct_error,
    )
