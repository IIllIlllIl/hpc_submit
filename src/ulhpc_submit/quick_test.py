"""Quick smoke and calibration result summaries."""

from __future__ import annotations

import re
from typing import List, Optional

from .usage import UsageJob


MEMORY_RE = re.compile(r"^([0-9]+(?:\.[0-9]+)?)([KMGTP]?)([cn]?)$", re.IGNORECASE)


def parse_memory_to_mib(value: str) -> Optional[float]:
    """Parse common Slurm memory strings into MiB.

    Slurm may report values like ``1024K``, ``512M``, ``4G``, or request
    strings with per-cpu/per-node suffixes such as ``4Gn``. Unknown values
    return ``None`` so callers can keep the original text without guessing.
    """
    text = value.strip()
    if not text or text in {"0", "Unknown", "None"}:
        return None
    match = MEMORY_RE.match(text)
    if not match:
        return None
    number = float(match.group(1))
    unit = match.group(2).upper() or "M"
    factors = {
        "K": 1 / 1024,
        "M": 1,
        "G": 1024,
        "T": 1024 * 1024,
        "P": 1024 * 1024 * 1024,
    }
    return number * factors[unit]


def calibration_lines(
    job: UsageJob,
    cpu_efficiency_threshold: float = 0.5,
    memory_headroom_threshold: float = 0.25,
) -> List[str]:
    """Return human-readable calibration metrics and sizing hints."""
    lines = [
        "[ulhpc-submit] Quick calibration analysis",
        f"Job: {job.job_id}; state: {job.base_state}; partition: {job.partition or 'unknown'}",
        f"Allocated core-hours: {job.core_hours:.2f}",
    ]

    efficiency = job.cpu_efficiency
    if efficiency is not None:
        lines.append(f"CPU efficiency: {efficiency * 100:.1f}%")
        if efficiency < cpu_efficiency_threshold:
            lines.append(
                "Hint: CPU efficiency is low; consider requesting fewer CPUs or improving parallelism."
            )
    else:
        lines.append("CPU efficiency: unavailable")

    lines.append(f"Memory: requested {job.req_mem or 'unknown'}; max RSS {job.max_rss or 'unknown'}")
    requested_mib = parse_memory_to_mib(job.req_mem)
    max_rss_mib = parse_memory_to_mib(job.max_rss)
    if requested_mib and max_rss_mib:
        used_fraction = max_rss_mib / requested_mib
        lines.append(f"Memory used/requested: {used_fraction * 100:.1f}%")
        if used_fraction < memory_headroom_threshold:
            lines.append(
                "Hint: memory request appears high for this calibration run; consider lowering --mem for production if the workload is representative."
            )

    if job.base_state == "OUT_OF_MEMORY":
        lines.append("Hint: calibration ran out of memory; increase --mem before production.")
    elif job.base_state == "TIMEOUT":
        lines.append("Hint: calibration hit the wallclock limit; shorten the test workload or increase --duration/--time.")
    elif job.base_state != "COMPLETED":
        lines.append("Hint: calibration did not complete; inspect logs before using these sizing metrics.")

    return lines
