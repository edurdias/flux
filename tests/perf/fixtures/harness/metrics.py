"""Metric math for the engine benchmarks (#259).

Kept separate from the scenarios that produce the samples: a percentile
that is subtly wrong makes every number the suite publishes wrong, and
these are the only parts that can be checked without a running server.

Percentiles are **nearest-rank**, deliberately: a tail figure should name
a request that actually happened, not an interpolation between two that
did. p99 of a hundred samples is the 99th slowest, so a number in a report
is one an operator could go and find in the log.
"""

from __future__ import annotations

import math
from statistics import fmean


def percentile(values: list[float], quantile: float) -> float:
    """The nearest-rank ``quantile``-th percentile of ``values``."""
    if not values:
        raise ValueError("percentile() of an empty series")
    if not 0 < quantile <= 100:
        raise ValueError(f"quantile must be in (0, 100], got {quantile}")
    ordered = sorted(values)
    rank = math.ceil(quantile / 100 * len(ordered))
    return ordered[max(rank - 1, 0)]


def latency_summary(values: list[float]) -> dict:
    """Count, headline quantiles and shape of one latency series.

    An empty series summarizes to a zero count rather than raising: a
    scenario that produced no samples still has to record its run, because
    "nothing was measured" is itself the finding.
    """
    if not values:
        return {
            "count": 0,
            "p50": None,
            "p95": None,
            "p99": None,
            "min": None,
            "max": None,
            "mean": None,
        }
    return {
        "count": len(values),
        "p50": percentile(values, 50),
        "p95": percentile(values, 95),
        "p99": percentile(values, 99),
        "min": min(values),
        "max": max(values),
        "mean": fmean(values),
    }


def throughput(count: int, seconds: float) -> float | None:
    """Completions per second, or None when the window has no duration."""
    if seconds <= 0:
        return None
    return count / seconds
