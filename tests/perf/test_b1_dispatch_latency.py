"""B1 — dispatch latency: how long an execution waits before it runs.

The headline is scheduled → started (p50/p95/p99): the scheduler tick or
event notification, the claim round trip, and the worker's module load,
with the workflow body excluded by construction. Claim latency and
scheduled → first task are recorded alongside so a regression can be
attributed to a half rather than to "dispatch".

Correctness gate: every submitted execution must reach a terminal state
and yield a latency sample -- a benchmark that silently measured half its
submissions is worse than none. Performance gates are soft per harness
policy.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fixtures.harness.bench import (
    claim_latency_ms,
    dispatch_latency_ms,
    first_task_latency_ms,
)
from fixtures.harness.metrics import latency_summary
from fixtures.harness.profile import params, profile_name, soft_gate
from fixtures.harness.report import write_run

FIXTURES = Path(__file__).parent / "fixtures"
NAMESPACE = "default"


@pytest.mark.perf
def test_b1_dispatch_latency(perf_env):
    spec = params("b1")
    perf_env.register(FIXTURES / "bench_workflow.py")

    executions = []
    for _ in range(spec["executions"]):
        started = perf_env.run_async(NAMESPACE, "bench_single", {"payload": spec["payload"]})
        executions.append(started["execution_id"])

    dispatch, claim, first_task = [], [], []
    for execution_id in executions:
        perf_env.wait_for_terminal(NAMESPACE, "bench_single", execution_id, timeout=180)
        detail = perf_env.status(NAMESPACE, "bench_single", execution_id, detailed=True)
        for series, value in (
            (dispatch, dispatch_latency_ms(detail)),
            (claim, claim_latency_ms(detail)),
            (first_task, first_task_latency_ms(detail)),
        ):
            if value is not None:
                series.append(value)

    summary = latency_summary(dispatch)
    gates = {
        # Correctness: every submission has to have been measured.
        "all_executions_sampled": len(dispatch) == len(executions),
        "dispatch_p95_under_budget": soft_gate(
            summary["p95"] is not None and summary["p95"] <= spec["p95_budget_ms"],
            f"dispatch p95 {summary['p95']}ms over budget {spec['p95_budget_ms']}ms",
        ),
    }

    write_run(
        "B1",
        f"{profile_name()}-dispatch",
        {
            "profile": profile_name(),
            "executions": len(executions),
            "payload_bytes": spec["payload"],
            "http_rtt_s": perf_env.measure_http_rtt(),
            "dispatch_ms": summary,
            "claim_ms": latency_summary(claim),
            "first_task_ms": latency_summary(first_task),
            "gates": gates,
        },
    )

    assert gates["all_executions_sampled"], (
        f"only {len(dispatch)}/{len(executions)} executions produced a dispatch sample"
    )
