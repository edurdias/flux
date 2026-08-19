"""B4 — event-loop responsiveness under load (#263).

A blocking call inside an async handler stalls the whole loop, so the cost
lands on *other* requests, not the one doing the blocking. Dispatch and
throughput cannot see that; they measure the work itself. This does: a
sampler pings a handler that touches nothing (`GET /health`) at a fixed
interval while a throughput workload runs, and reports how long those
pings take.

An idle server answers /health in about one RTT. Every millisecond above
that is the loop being held by something else -- which makes the p99 here
the number sync-in-async work is judged by.

Correctness gate: the workload must complete and the sampler must collect
samples throughout. Latency gates are soft per harness policy.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest

from fixtures.harness.bench import completed_tasks
from fixtures.harness.metrics import latency_summary
from fixtures.harness.profile import params, profile_name, soft_gate
from fixtures.harness.report import write_run

FIXTURES = Path(__file__).parent / "fixtures"
NAMESPACE = "default"


class _HealthSampler(threading.Thread):
    """Pings /health on its own thread, recording each round trip in ms."""

    def __init__(self, server_url: str, interval_s: float):
        super().__init__(daemon=True)
        self.server_url = server_url
        self.interval_s = interval_s
        self.samples: list[float] = []
        self._stop = threading.Event()

    def run(self) -> None:
        with httpx.Client(base_url=self.server_url, timeout=30) as client:
            while not self._stop.is_set():
                started = time.perf_counter()
                try:
                    client.get("/health")
                except httpx.HTTPError:
                    # A refused ping is not a latency sample; the workload's
                    # own gates decide whether the run is valid.
                    continue
                finally:
                    self.samples.append((time.perf_counter() - started) * 1000)
                self._stop.wait(self.interval_s)

    def stop(self) -> None:
        self._stop.set()
        self.join(timeout=10)


@pytest.mark.perf
def test_b4_loop_responsiveness_under_load(perf_env):
    spec = params("b4")
    perf_env.register(FIXTURES / "bench_workflow.py")

    idle = _HealthSampler(perf_env.server_url, spec["sample_interval_s"])
    idle.start()
    time.sleep(spec["idle_seconds"])
    idle.stop()

    under_load = _HealthSampler(perf_env.server_url, spec["sample_interval_s"])
    under_load.start()
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
    under_load.stop()

    idle_summary = latency_summary(idle.samples)
    load_summary = latency_summary(under_load.samples)
    tasks_done = sum(completed_tasks(detail) for detail in details)
    expected = spec["workflows"] * spec["tasks_per_workflow"]

    gates = {
        "workload_completed": tasks_done == expected,
        "sampled_throughout": len(under_load.samples) >= spec["min_samples"],
        "p99_within_budget": soft_gate(
            load_summary["p99"] is not None and load_summary["p99"] <= spec["p99_budget_ms"],
            f"health p99 under load {load_summary['p99']}ms over budget {spec['p99_budget_ms']}ms",
        ),
    }

    write_run(
        "B4",
        f"{profile_name()}-loop-responsiveness",
        {
            "profile": profile_name(),
            "backend": perf_env.backend,
            "workflows": spec["workflows"],
            "tasks_per_workflow": spec["tasks_per_workflow"],
            "http_rtt_s": perf_env.measure_http_rtt(),
            "health_ms_idle": idle_summary,
            "health_ms_under_load": load_summary,
            # The headline: how much worse a do-nothing request gets when
            # the loop is busy. 1.0 would mean the loop never blocked.
            "p99_inflation": (
                load_summary["p99"] / idle_summary["p99"]
                if idle_summary["p99"] and load_summary["p99"]
                else None
            ),
            "gates": gates,
        },
    )

    assert gates["workload_completed"], (
        f"{tasks_done}/{expected} tasks completed -- the latency above is not comparable"
    )
    assert gates["sampled_throughout"], (
        f"only {len(under_load.samples)} health samples collected, "
        f"expected at least {spec['min_samples']}"
    )
