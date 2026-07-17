"""Run profiles and gate policy.

Two profiles select the measurement windows:

- ``ci`` (default): short windows sized for a shared pipeline runner. The
  point of a CI run is "where are we" — real numbers from a noisy small box,
  not certification.
- ``full``: the plan-spec durations (PLAN.md §2), for quiet dedicated
  hardware.

Gate policy: correctness assertions (persistence, terminal states, no
protocol wedge) always hard-fail. *Performance* gates are soft by default —
measured, recorded in results with pass/fail, but they only fail the test
run when FLUX_PERF_STRICT=1 (the dedicated-environment mode).
"""

from __future__ import annotations

import os

PROFILES: dict[str, dict] = {
    "ci": {
        "t1": {"frame_sizes": [150, 2048], "rates": [500, 1000, 2000, 0], "seconds": 8},
        "t2": {"streams": 8, "rate": 100, "seconds": 30, "solo_seconds": 15},
        "t3": {"targets": 6, "steps": [1000, 3000, 6000, 10000], "step_seconds": 15},
        "t4": {"tokens": 200, "gap_ms": 33, "streams": 8},
        "t5": {"streams": 8, "rate": 100, "seconds": 40, "throttle_bytes_per_s": 1024},
        "t5b": {"rate": 50, "seconds": 25},
        "t6a": {"streams": 8, "rate": 200, "cancel_after_s": 5},
        "t6b": {"detect_timeout_s": 90},
        # drop_for must stay under the violence env's 6s heartbeat timeout to
        # exercise reconnect-and-resume; the eviction fate is full-profile's.
        "t6c": {"rate": 100, "seconds": 30, "drop_after_s": 8, "drop_for_s": 2},
        "t7": {"streams": 3, "rate": 100, "seconds": 240, "churn_every_s": 20},
    },
    "full": {
        "t1": {"frame_sizes": [150, 2048], "rates": [500, 1000, 2000, 4000, 0], "seconds": 60},
        "t2": {"streams": 8, "rate": 100, "seconds": 120, "solo_seconds": 60},
        "t3": {"targets": 8, "steps": [1000, 3000, 10000, 30000], "step_seconds": 180},
        "t4": {"tokens": 2000, "gap_ms": 33, "streams": 8},
        "t5": {"streams": 8, "rate": 100, "seconds": 120, "throttle_bytes_per_s": 1024},
        "t5b": {"rate": 50, "seconds": 40},
        "t6a": {"streams": 8, "rate": 200, "cancel_after_s": 10},
        "t6b": {"detect_timeout_s": 180},
        "t6c": {"rate": 100, "seconds": 60, "drop_after_s": 15, "drop_for_s": 10},
        "t7": {"streams": 10, "rate": 300, "seconds": 3600, "churn_every_s": 30},
    },
}


def profile_name() -> str:
    return os.environ.get("FLUX_PERF_PROFILE", "ci")


def params(test: str) -> dict:
    return PROFILES[profile_name()][test]


def strict() -> bool:
    return bool(os.environ.get("FLUX_PERF_STRICT"))


def soft_gate(passed: bool, message: str) -> bool:
    """Record-or-fail a performance gate.

    Returns the verdict for inclusion in results; raises only in strict mode
    so a noisy box produces numbers instead of red pipelines.
    """
    if strict():
        assert passed, message
    return passed
