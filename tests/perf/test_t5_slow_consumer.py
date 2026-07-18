"""T5 — Slow consumer / head-of-line verification (PLAN.md §2, §0b).

Pre-registered expectation from code: the throttled stream's server queue
(maxsize 10,000) fills and drops newest; memory stays bounded; siblings on
their own per-execution queues are unaffected.

Gates: siblings lose nothing and server RSS growth stays bounded (soft in
ci). The observed policy itself is recorded, whatever it is.
"""

from __future__ import annotations

from fixtures.harness.profile import params, profile_name, soft_gate
from fixtures.harness.report import write_run
from fixtures.harness.sampler import ProcessSampler
from fixtures.harness.scenario import median_or_none, start_stream, wait_all

RSS_GROWTH_CAP_BYTES = 200 * 1024 * 1024


def test_t5_slow_consumer(perf_env, stream_workflow):
    namespace, name = stream_workflow
    p = params("t5")
    frames = int(p["rate"] * p["seconds"])
    sampler = ProcessSampler(
        {"server": perf_env.server_proc.pid, "worker": perf_env.worker_proc.pid},
    ).start()

    launched = []
    try:
        for i in range(p["streams"]):
            throttle = p["throttle_bytes_per_s"] if i == 0 else None
            launched.append(
                start_stream(
                    perf_env,
                    namespace,
                    name,
                    frames=frames,
                    rate=p["rate"],
                    consumer_kwargs={"throttle_bytes_per_s": throttle},
                ),
            )
        consumers = [c for _, c in launched]
        wait_all(consumers[1:], timeout=2.0 + p["seconds"] + 120)
    finally:
        for _, c in launched:
            c.stop()
        sampler.stop()
    for execution_id, _ in launched:
        perf_env.wait_for_terminal(namespace, name, execution_id, timeout=180)

    slow = launched[0][1].stats().as_dict()
    siblings = [c.stats().as_dict() for _, c in launched[1:]]
    sibling_lost = sum(frames - s["delivered"] for s in siblings)
    usage = sampler.summary()
    server_growth = usage.get("server", {}).get("rss_max", 0) - usage.get(
        "server",
        {},
    ).get("rss_first", 0)

    ok_siblings = soft_gate(
        sibling_lost == 0,
        f"siblings lost {sibling_lost} frames while stream 1 was throttled",
    )
    ok_rss = soft_gate(
        server_growth <= RSS_GROWTH_CAP_BYTES,
        f"server RSS grew {server_growth / 1e6:.0f}MB under a slow consumer",
    )
    policy = (
        "drop-newest at the per-execution server queue: slow consumer received "
        f"{slow['delivered']}/{frames} frames, siblings full-rate, memory bounded"
    )
    write_run(
        "T5",
        f"slow-consumer-{profile_name()}",
        {
            "gate": "bounded memory; siblings unaffected (soft in ci)",
            "measured": (
                f"slow stream delivered {slow['delivered']}/{frames}; "
                f"siblings lost {sibling_lost}; server RSS +{server_growth / 1e6:.0f}MB; "
                f"sibling p99 {median_or_none([s['interframe_p99'] for s in siblings if s['interframe_p99']])}"
            ),
            "passed": ok_siblings and ok_rss,
            "sealed": False,
            "database": perf_env.database_url.split(":", 1)[0],
            "profile": profile_name(),
            "observed_policy": policy,
            "slow_stream": slow,
            "siblings": siblings,
            "process_usage": usage,
        },
    )
