"""T7 — Soak: sustained load with sequential consumer churn (PLAN.md §2).

Long streams at steady aggregate rate while each stream's consumer
disconnects and reconnects on a cadence — strictly sequentially per
execution (never two consumers on one execution; that design limit is
T5b's subject). Frames arriving between a disconnect and the next
subscribe are discarded by design and excluded from loss accounting.

Gates (soft in ci): RSS drift ≤ 10% after warmup; event-store growth ≈
constant (T0's long-run form, hard); p99 drift first-vs-last window ≤ 20%.
"""

from __future__ import annotations

import time

from fixtures.harness.consumer import StreamConsumer, percentile
from fixtures.harness.dbmeter import create_meter, diff
from fixtures.harness.profile import params, profile_name, soft_gate
from fixtures.harness.report import write_run
from fixtures.harness.sampler import ProcessSampler
from fixtures.harness.scenario import start_stream

RSS_DRIFT_GATE = 0.10
P99_DRIFT_GATE = 0.20


def _rss_drift(samples, process: str, warmup_s: float) -> float | None:
    series = [s for s in samples if s.process == process]
    if len(series) < 10:
        return None
    t0 = series[0].t + warmup_s
    settled = [s.rss_bytes for s in series if s.t >= t0]
    if len(settled) < 5:
        return None
    head = settled[: max(1, len(settled) // 5)]
    tail = settled[-max(1, len(settled) // 5) :]
    base = sum(head) / len(head)
    return (sum(tail) / len(tail) - base) / base if base else None


def test_t7_soak(perf_env, stream_workflow):
    namespace, name = stream_workflow
    p = params("t7")
    frames = int(p["rate"] * p["seconds"])
    meter = create_meter(perf_env.database_url, perf_env.db_path)
    sampler = ProcessSampler(
        {"server": perf_env.server_proc.pid, "worker": perf_env.worker_proc.pid},
    ).start()
    before = meter.snapshot()

    launched = [
        start_stream(perf_env, namespace, name, frames=frames, rate=p["rate"])
        for _ in range(p["streams"])
    ]
    execution_ids = [e for e, _ in launched]
    consumers = {e: c for e, c in launched}
    retired: list[StreamConsumer] = []
    all_latencies: list[tuple[float, float]] = []  # (recv_ts, latency)

    end_at = time.monotonic() + 2.0 + p["seconds"]
    try:
        while time.monotonic() < end_at:
            time.sleep(p["churn_every_s"])
            # Sequential churn: fully disconnect, then resubscribe.
            for execution_id in execution_ids:
                old = consumers[execution_id]
                old.stop()
                retired.append(old)
                consumers[execution_id] = StreamConsumer(
                    perf_env.server_url,
                    execution_id,
                ).start()
    finally:
        for c in list(consumers.values()):
            c.wait(60)
            c.stop()
        sampler.stop()
    for execution_id in execution_ids:
        perf_env.wait_for_terminal(namespace, name, execution_id, timeout=180)
    after = meter.snapshot()

    for c in retired + list(consumers.values()):
        all_latencies.extend(
            (f.t_recv, f.t_recv - f.t_sent) for f in c.frames if f.t_sent is not None
        )
    all_latencies.sort()
    fifth = max(1, len(all_latencies) // 5)
    p99_first = percentile([lat for _, lat in all_latencies[:fifth]], 0.99)
    p99_last = percentile([lat for _, lat in all_latencies[-fifth:]], 0.99)
    p99_drift = (p99_last - p99_first) / p99_first if p99_first and p99_last else None
    delivered = len(all_latencies)

    store_delta = diff(before, after)
    event_rows = store_delta["rows"].get("execution_events", 0)
    # T0's long-run form (hard): bounded per-execution footprint, no
    # progress-proportional growth. 6 rows/execution + margin.
    assert event_rows <= 10 * p["streams"], (
        f"event store grew by {event_rows} rows during soak — progress-proportional growth"
    )

    drift_server = _rss_drift(sampler.samples, "server", warmup_s=60)
    drift_worker = _rss_drift(sampler.samples, "worker", warmup_s=60)
    ok_rss = soft_gate(
        all(d is None or d <= RSS_DRIFT_GATE for d in (drift_server, drift_worker)),
        f"RSS drift server={drift_server} worker={drift_worker} exceeds {RSS_DRIFT_GATE}",
    )
    ok_p99 = soft_gate(
        p99_drift is None or p99_drift <= P99_DRIFT_GATE,
        f"p99 drift {p99_drift} exceeds {P99_DRIFT_GATE}",
    )

    write_run(
        "T7",
        f"soak-{profile_name()}",
        {
            "gate": "RSS drift <= 10%, p99 drift <= 20% (soft in ci); store growth bounded (hard)",
            "measured": (
                f"{p['streams']}x{p['rate']} ev/s for {p['seconds']}s with "
                f"{p['churn_every_s']}s sequential churn; delivered {delivered}; "
                f"RSS drift server {drift_server}, worker {drift_worker}; "
                f"p99 drift {p99_drift}; store +{event_rows} event rows"
            ),
            "passed": ok_rss and ok_p99,
            "sealed": False,
            "database": perf_env.database_url.split(":", 1)[0],
            "profile": profile_name(),
            "delivered": delivered,
            "p99_first_window": p99_first,
            "p99_last_window": p99_last,
            "p99_drift": p99_drift,
            "rss_drift": {"server": drift_server, "worker": drift_worker},
            "db_delta": store_delta,
            "process_usage": sampler.summary(),
        },
    )
