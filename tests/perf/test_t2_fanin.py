"""T2 — Worker fan-in: 8 concurrent streams through one worker (PLAN.md §2).

Measures aggregate delivered rate, per-stream inter-frame p99, and the
interference delta (solo p99 vs p99 with 7 saturated siblings). A second,
shorter pass runs against an event-dispatch-mode server for delivery-path
parity.

Gates (soft in ci): no loss at the offered rate; interference delta ≤ 10 ms.
"""

from __future__ import annotations

import os

import pytest

from fixtures.harness.profile import params, profile_name, soft_gate
from fixtures.harness.report import write_run
from fixtures.harness.scenario import (
    delivered_rate,
    median_or_none,
    start_stream,
    wait_all,
)

INTERFERENCE_GATE_S = 0.010


def _run_streams(env, namespace, name, streams, rate, seconds) -> list[dict]:
    frames = int(rate * seconds)
    launched = [
        start_stream(env, namespace, name, frames=frames, rate=rate) for _ in range(streams)
    ]
    consumers = [c for _, c in launched]
    wait_all(consumers, timeout=2.0 + seconds + 120)
    for c in consumers:
        c.stop()
    for execution_id, _ in launched:
        env.wait_for_terminal(namespace, name, execution_id, timeout=120)
    out = []
    for execution_id, c in launched:
        stats = c.stats().as_dict()
        out.append(
            {
                "execution_id": execution_id,
                "frames_offered": frames,
                "delivered": stats["delivered"],
                "lost": frames - stats["delivered"],
                "interframe_p99": stats["interframe_p99"],
                "latency_p99": stats["latency_p99"],
                "delivered_rate": delivered_rate(stats),
            },
        )
    return out


def test_t2_worker_fanin(perf_env, stream_workflow):
    namespace, name = stream_workflow
    p = params("t2")

    solo = _run_streams(perf_env, namespace, name, 1, p["rate"], p["solo_seconds"])[0]
    saturated = _run_streams(
        perf_env,
        namespace,
        name,
        p["streams"],
        p["rate"],
        p["seconds"],
    )

    total_lost = sum(s["lost"] for s in saturated)
    aggregate = sum(s["delivered_rate"] or 0 for s in saturated)
    p99s = [s["interframe_p99"] for s in saturated if s["interframe_p99"]]
    delta = (median_or_none(p99s) or 0) - (solo["interframe_p99"] or 0) if p99s else None

    ok_loss = soft_gate(total_lost == 0, f"{total_lost} frames lost across streams")
    ok_intf = soft_gate(
        delta is not None and delta <= INTERFERENCE_GATE_S,
        f"interference delta {delta} > {INTERFERENCE_GATE_S}s",
    )

    write_run(
        "T2",
        f"fanin-{profile_name()}",
        {
            "gate": "no loss; interference p99 delta <= 10ms (soft in ci)",
            "measured": (
                f"aggregate {aggregate:.0f} ev/s over {p['streams']} streams; "
                f"lost {total_lost}; interference delta "
                f"{'n/a' if delta is None else f'{delta * 1000:.1f}ms'}"
            ),
            "passed": ok_loss and ok_intf,
            "sealed": False,
            "database": perf_env.database_url.split(":", 1)[0],
            "profile": profile_name(),
            "solo": solo,
            "streams": saturated,
        },
    )


@pytest.mark.slow
def test_t2_event_dispatch_parity(tmp_path_factory, stream_workflow_source):
    """Same fan-in shape against an event-dispatch-mode server."""
    from fixtures.harness.env import FluxPerfEnv

    p = params("t2")
    seconds = min(p["seconds"], 20)
    workdir = tmp_path_factory.mktemp("perf-event")
    env = FluxPerfEnv(
        workdir,
        database_url=os.environ.get("FLUX_PERF_DATABASE_URL_EVENT")
        or os.environ.get("FLUX_PERF_DATABASE_URL"),
        worker_name="perf-worker-event",
        env_overrides={"FLUX_DISPATCH__MODE": "event"},
    ).start()
    try:
        result = env.register(stream_workflow_source)
        entries = result if isinstance(result, list) else [result]
        namespace = next(
            (
                e.get("namespace", "default")
                for e in entries
                if isinstance(e, dict) and e.get("name") == "perf_stream"
            ),
            "default",
        )
        streams = _run_streams(
            env,
            namespace,
            "perf_stream",
            p["streams"],
            p["rate"],
            seconds,
        )
        total_lost = sum(s["lost"] for s in streams)
        passed = soft_gate(total_lost == 0, f"{total_lost} frames lost in event mode")
        write_run(
            "T2-event",
            f"parity-{profile_name()}",
            {
                "gate": "delivery parity in event dispatch mode (soft in ci)",
                "measured": (
                    f"{sum(s['delivered'] for s in streams)}/"
                    f"{sum(s['frames_offered'] for s in streams)} delivered; "
                    f"lost {total_lost}"
                ),
                "passed": passed,
                "sealed": False,
                "database": env.database_url.split(":", 1)[0],
                "profile": profile_name(),
                "streams": streams,
            },
        )
    finally:
        env.stop()
