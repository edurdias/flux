"""B2 — sustained throughput: tasks/second through the whole pipe.

N workflows are launched concurrently, each a chain of tasks, and the rate
is measured over the window in which work is actually in flight -- first
task start to last task completion, both server-stamped -- so submission
and drain ramps do not deflate the figure.

Correctness gate: every execution completes and the task count persisted
matches what was asked for. Rate gates are soft per harness policy: on a
shared runner this number is a "where are we", not a certification.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from fixtures.harness.bench import completed_tasks, first_time, last_time
from fixtures.harness.metrics import throughput
from fixtures.harness.profile import params, profile_name, soft_gate
from fixtures.harness.report import write_run

FIXTURES = Path(__file__).parent / "fixtures"
NAMESPACE = "default"


@pytest.mark.perf
def test_b2_sustained_throughput(perf_env):
    spec = params("b2")
    perf_env.register(FIXTURES / "bench_workflow.py")

    fds_before = perf_env.open_fds()
    payload = {"tasks": spec["tasks_per_workflow"], "payload": spec["payload"]}
    with ThreadPoolExecutor(max_workers=spec["concurrency"]) as pool:
        submissions = [
            pool.submit(perf_env.run_async, NAMESPACE, "bench_chain", payload)
            for _ in range(spec["workflows"])
        ]
        execution_ids = [future.result()["execution_id"] for future in submissions]

    details = []
    for execution_id in execution_ids:
        perf_env.wait_for_terminal(NAMESPACE, "bench_chain", execution_id, timeout=600)
        details.append(perf_env.status(NAMESPACE, "bench_chain", execution_id, detailed=True))

    # Counted after the fleet has gone quiet, not the moment the last task
    # lands: keep-alive is supposed to hold connections open, so a count
    # taken mid-flight cannot tell a pool from a leak. What must not grow
    # is the idle-to-idle figure.
    time.sleep(spec.get("settle_seconds", 3))
    fds_after = perf_env.open_fds()
    starts = [first_time(detail, "TASK_STARTED") for detail in details]
    ends = [last_time(detail, "TASK_COMPLETED") for detail in details]
    # An execution missing either stamp would shrink the window while its
    # tasks still counted toward the numerator -- an overstated rate that
    # looks like a healthy one. The numbers are only comparable if every
    # execution contributed both ends of its own window.
    fully_stamped = sum(
        1 for start, end in zip(starts, ends) if start is not None and end is not None
    )
    starts = [value for value in starts if value is not None]
    ends = [value for value in ends if value is not None]

    tasks_done = sum(completed_tasks(detail) for detail in details)
    expected = spec["workflows"] * spec["tasks_per_workflow"]
    # The window work was actually in flight, not wall time including ramps.
    window_s = (max(ends) - min(starts)).total_seconds() if starts and ends else 0.0
    rate = throughput(tasks_done, window_s)

    gates = {
        "every_task_persisted": tasks_done == expected,
        "every_execution_timed": fully_stamped == len(details),
        "throughput_over_floor": soft_gate(
            rate is not None and rate >= spec["min_tasks_per_s"],
            f"throughput {rate} tasks/s under floor {spec['min_tasks_per_s']}",
        ),
    }

    write_run(
        "B2",
        f"{profile_name()}-throughput",
        {
            "profile": profile_name(),
            "backend": perf_env.backend,
            "workflows": spec["workflows"],
            "tasks_per_workflow": spec["tasks_per_workflow"],
            "submit_concurrency": spec["concurrency"],
            "payload_bytes": spec["payload"],
            "http_rtt_s": perf_env.measure_http_rtt(),
            "tasks_completed": tasks_done,
            "tasks_expected": expected,
            "executions_fully_timed": fully_stamped,
            "in_flight_window_s": window_s,
            "tasks_per_s": rate,
            # Pooling check (#261): sockets held before vs after the load.
            "open_fds_idle_before": fds_before,
            "open_fds_idle_after": fds_after,
            # None counts mean the check could not run (not Linux, or the
            # process was gone). Recorded explicitly so a null pair is never
            # read as "no growth" -- absence of a measurement is not evidence.
            "open_fds_measured": all(
                fds_before.get(role) is not None and fds_after.get(role) is not None
                for role in ("server", "worker")
            ),
            "gates": gates,
        },
    )

    assert gates["every_task_persisted"], (
        f"{tasks_done} tasks persisted, expected {expected} -- the rate above is not comparable"
    )
    assert gates["every_execution_timed"], (
        f"only {fully_stamped}/{len(details)} executions carried both timing stamps -- "
        "the in-flight window is narrower than the work it is dividing"
    )
