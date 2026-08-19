"""Run profiles and gate policy.

Two profiles select the measurement windows:

- ``ci`` (default): short windows sized for a shared pipeline runner. The
  point of a CI run is "where are we" — real numbers from a noisy small box,
  not certification.
- ``workstation``: a strong multi-core dev box (think a 16–32 core laptop
  or desktop). Probes high enough to actually locate the ceiling/knee that
  ``ci`` never reaches on fast hardware, with moderate windows so the whole
  suite finishes in ~20–30 min instead of ``full``'s ~90.
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
        # B series (#259): engine benchmarks. ci sizes are "where are we"
        # on a shared runner -- small enough to finish in a PR pipeline,
        # large enough that a percentile means something.
        "b1": {"executions": 40, "payload": 64, "p95_budget_ms": 3000},
        "b2": {
            "workflows": 20,
            "tasks_per_workflow": 10,
            "concurrency": 8,
            "payload": 64,
            "min_tasks_per_s": 1.0,
        },
        "b3": {"histories": [25, 100], "payload": 64, "linearity_factor": 3.0},
        "t8": {
            "samples": 5,
            "budget_ms": {"flux.server": 2500, "flux.worker": 1200, "flux.runners.child": 400},
            "container_budget_ms": {
                "flux.server": 4000,
                "flux.worker": 2500,
                "flux.runners.child": 1200,
            },
        },
    },
    "workstation": {
        # Push offered rates past the ci ceiling so the loss-onset curve and
        # the server knee are actually resolved on a 16–32 core box.
        "t1": {
            "frame_sizes": [150, 2048],
            "rates": [1000, 2000, 4000, 8000, 16000, 0],
            "seconds": 30,
        },
        "t2": {"streams": 16, "rate": 200, "seconds": 60, "solo_seconds": 30},
        "t3": {"targets": 8, "steps": [5000, 15000, 30000, 60000], "step_seconds": 45},
        "t4": {"tokens": 1000, "gap_ms": 33, "streams": 16},
        "t5": {"streams": 16, "rate": 200, "seconds": 60, "throttle_bytes_per_s": 1024},
        "t5b": {"rate": 100, "seconds": 30},
        "t6a": {"streams": 16, "rate": 300, "cancel_after_s": 8},
        "t6b": {"detect_timeout_s": 90},
        # drop_for stays under the violence env's 6s heartbeat timeout so this
        # still exercises reconnect-and-resume (eviction fate is full's job).
        "t6c": {"rate": 200, "seconds": 45, "drop_after_s": 10, "drop_for_s": 3},
        "t7": {"streams": 8, "rate": 300, "seconds": 600, "churn_every_s": 30},
        "b1": {"executions": 100, "payload": 256, "p95_budget_ms": 1500},
        "b2": {
            "workflows": 60,
            "tasks_per_workflow": 20,
            "concurrency": 16,
            "payload": 256,
            "min_tasks_per_s": 5.0,
        },
        "b3": {"histories": [50, 200, 500], "payload": 256, "linearity_factor": 2.5},
        "t8": {
            "samples": 9,
            "budget_ms": {"flux.server": 1500, "flux.worker": 700, "flux.runners.child": 200},
            "container_budget_ms": {
                "flux.server": 2500,
                "flux.worker": 1500,
                "flux.runners.child": 700,
            },
        },
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
        "b1": {"executions": 300, "payload": 1024, "p95_budget_ms": 1000},
        "b2": {
            "workflows": 200,
            "tasks_per_workflow": 25,
            "concurrency": 32,
            "payload": 1024,
            "min_tasks_per_s": 10.0,
        },
        "b3": {"histories": [100, 500, 2000], "payload": 1024, "linearity_factor": 2.0},
        "t7": {"streams": 10, "rate": 300, "seconds": 3600, "churn_every_s": 30},
        "t8": {
            "samples": 15,
            "budget_ms": {"flux.server": 1500, "flux.worker": 700, "flux.runners.child": 200},
            "container_budget_ms": {
                "flux.server": 2500,
                "flux.worker": 1500,
                "flux.runners.child": 700,
            },
        },
    },
}


def profile_name() -> str:
    return os.environ.get("FLUX_PERF_PROFILE", "ci")


def params(test: str) -> dict:
    return PROFILES[profile_name()][test]


def strict() -> bool:
    # Only genuinely-truthy strings enable strict mode; `FLUX_PERF_STRICT=0`
    # or `=false` must not accidentally hard-fail perf runs.
    return os.environ.get("FLUX_PERF_STRICT", "").strip().lower() in {"1", "true", "yes", "on"}


def soft_gate(passed: bool, message: str) -> bool:
    """Record-or-fail a performance gate.

    Returns the verdict for inclusion in results; raises only in strict mode
    so a noisy box produces numbers instead of red pipelines.
    """
    if strict():
        assert passed, message
    return passed
