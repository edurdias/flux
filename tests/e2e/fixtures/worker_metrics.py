"""Metrics provider fixture: each e2e worker reports the fitness value from
its own environment, so tests can give two workers different scores."""

from __future__ import annotations

import os


def collect() -> dict[str, float]:
    return {
        "fitness": float(os.environ.get("FLUX_TEST_FITNESS", "0")),
        # Impersonation attempt: reserved-prefix keys must be stripped and
        # the genuine built-in value published instead.
        "flux.running_executions": 999.0,
    }
