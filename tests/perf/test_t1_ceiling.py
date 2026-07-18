"""T1 — Single-child protocol ceiling + loss-onset curve (PLAN.md §2).

One execution at a time emits frames at stepped rates (0 = uncapped) through
the real child → worker → server → SSE pipeline. Loss above the onset rate is
designed drop-newest policy, not failure — the deliverable is the curve.

Gate (soft in ci profile): ≥1,000 ev/s delivered without loss at 150 B.
"""

from __future__ import annotations

from fixtures.harness.profile import params, profile_name, soft_gate
from fixtures.harness.report import write_run
from fixtures.harness.sampler import ProcessSampler
from fixtures.harness.scenario import delivered_rate, offered_rate, start_stream

GATE_RATE = 1000.0


def _run_point(perf_env, namespace, name, rate, seconds, size) -> dict:
    frames = int(rate * seconds) if rate > 0 else int(20_000 if seconds <= 10 else 60_000)
    execution_id, consumer = start_stream(
        perf_env,
        namespace,
        name,
        frames=frames,
        rate=rate,
        size=size,
    )
    timeout = 2.0 + (frames / rate if rate > 0 else 60) + 90
    consumer.wait(timeout)
    consumer.stop()
    perf_env.wait_for_terminal(namespace, name, execution_id, timeout=120)
    stats = consumer.stats().as_dict()
    return {
        "execution_id": execution_id,
        "rate_requested": rate or "uncapped",
        "frames_offered": frames,
        "delivered": stats["delivered"],
        "lost": frames - stats["delivered"],
        "offered_rate_actual": offered_rate(consumer),
        "delivered_rate": delivered_rate(stats),
        "latency_p50": stats["latency_p50"],
        "latency_p99": stats["latency_p99"],
    }


def test_t1_single_child_ceiling(perf_env, stream_workflow):
    namespace, name = stream_workflow
    p = params("t1")
    sampler = ProcessSampler(
        {"server": perf_env.server_proc.pid, "worker": perf_env.worker_proc.pid},
    ).start()

    curves: dict[str, list[dict]] = {}
    try:
        for size in p["frame_sizes"]:
            curves[str(size)] = [
                _run_point(perf_env, namespace, name, rate, p["seconds"], size)
                for rate in p["rates"]
            ]
    finally:
        sampler.stop()

    lossless_150 = [
        pt["delivered_rate"]
        for pt in curves[str(p["frame_sizes"][0])]
        if pt["lost"] == 0 and pt["delivered_rate"]
    ]
    best_lossless = max(lossless_150, default=0.0)
    passed = soft_gate(
        best_lossless >= GATE_RATE,
        f"best lossless delivered rate {best_lossless:.0f} ev/s < {GATE_RATE:.0f}",
    )

    onset = next(
        (pt["rate_requested"] for pt in curves[str(p["frame_sizes"][0])] if pt["lost"] > 0),
        None,
    )
    write_run(
        "T1",
        f"ceiling-{profile_name()}",
        {
            "gate": f">= {GATE_RATE:.0f} ev/s delivered lossless @150B (soft in ci)",
            "measured": (
                f"best lossless {best_lossless:.0f} ev/s @150B; "
                f"loss onset at {onset or 'none observed'}"
            ),
            "passed": passed,
            "sealed": False,
            "database": perf_env.database_url.split(":", 1)[0],
            "profile": profile_name(),
            "curves": curves,
            "process_usage": sampler.summary(),
        },
    )
