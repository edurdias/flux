"""B3 — replay cost: resuming an execution with a long event history.

Resume is where the event log stops being free: before the workflow can
continue, every completed task in the history has to be short-circuited
from it. This measures that as a function of history length, which is the
figure any future change to event storage or replay has to be judged
against.

Two history sizes are run so the shape is visible: a replay that is linear
in history is expected, one that is quadratic is a finding.

Correctness gate: the resumed execution completes, and the tasks in its
history are not re-run (the replay short-circuit is the thing under test).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fixtures.harness.bench import completed_tasks, interval_ms, last_time
from fixtures.harness.profile import params, profile_name, soft_gate
from fixtures.harness.report import write_run

FIXTURES = Path(__file__).parent / "fixtures"
NAMESPACE = "default"


def _run_to_pause(perf_env, tasks: int, payload: int) -> str:
    started = perf_env.run_async(
        NAMESPACE,
        "bench_resumable",
        {"tasks": tasks, "payload": payload},
    )
    execution_id = started["execution_id"]
    perf_env.wait_for_state(NAMESPACE, "bench_resumable", execution_id, "PAUSED", timeout=600)
    return execution_id


def _replay_once(perf_env, tasks: int, payload: int) -> dict:
    execution_id = _run_to_pause(perf_env, tasks, payload)
    before = perf_env.status(NAMESPACE, "bench_resumable", execution_id, detailed=True)
    # The workload's own tasks only: pause() is a task too, and it completes
    # on resume -- counting it makes a clean replay look like a re-run.
    tasks_before = completed_tasks(before, name="bench_step")

    perf_env.resume(NAMESPACE, "bench_resumable", execution_id)
    perf_env.wait_for_terminal(NAMESPACE, "bench_resumable", execution_id, timeout=600)
    after = perf_env.status(NAMESPACE, "bench_resumable", execution_id, detailed=True)

    # Server-stamped: resume scheduled → workflow completed. The tasks in
    # the history are not re-run, so this interval is replay plus the tail.
    replay_ms = interval_ms(
        last_time(after, "WORKFLOW_RESUME_SCHEDULED"),
        last_time(after, "WORKFLOW_COMPLETED"),
    )
    return {
        "tasks": tasks,
        "tasks_before_resume": tasks_before,
        "tasks_after_resume": completed_tasks(after, name="bench_step"),
        "replay_ms": replay_ms,
        "execution_id": execution_id,
    }


@pytest.mark.perf
def test_b3_replay_cost(perf_env):
    spec = params("b3")
    perf_env.register(FIXTURES / "bench_workflow.py")

    runs = [_replay_once(perf_env, tasks, spec["payload"]) for tasks in spec["histories"]]

    # Replay must not re-execute what the log already records; a re-run
    # would show up as more TASK_COMPLETED events after the resume.
    no_reexecution = all(run["tasks_after_resume"] == run["tasks_before_resume"] for run in runs)

    measured = sorted(
        (run for run in runs if run["replay_ms"] is not None),
        key=lambda run: run["tasks"],
    )
    # Average cost per task is dominated by the fixed resume overhead at
    # small histories, so it says nothing about scaling. The *marginal*
    # cost -- what each additional task of history adds -- is the shape
    # that separates a linear replay from a quadratic one.
    marginal = [
        ((later["replay_ms"] - earlier["replay_ms"]) / (later["tasks"] - earlier["tasks"]))
        for earlier, later in zip(measured, measured[1:])
        if later["tasks"] > earlier["tasks"]
    ]
    positive = [value for value in marginal if value > 0]
    gates = {
        "no_task_reexecuted_on_replay": no_reexecution,
        # Needs three histories to have two marginals to compare; the ci
        # profile runs two, so it records the number and gates nothing.
        "replay_scales_linearly": soft_gate(
            max(positive) <= min(positive) * spec["linearity_factor"],
            f"marginal replay cost grew {max(positive) / min(positive):.1f}x across "
            f"histories {spec['histories']} -- superlinear replay",
        )
        if len(positive) >= 2
        else None,
    }

    write_run(
        "B3",
        f"{profile_name()}-replay",
        {
            "profile": profile_name(),
            "payload_bytes": spec["payload"],
            "http_rtt_s": perf_env.measure_http_rtt(),
            "runs": runs,
            "marginal_ms_per_task": marginal,
            "avg_ms_per_task": [
                run["replay_ms"] / run["tasks"] for run in measured if run["tasks"]
            ],
            "gates": gates,
        },
    )

    assert gates["no_task_reexecuted_on_replay"], (
        "replay re-executed tasks the event log already recorded"
    )
