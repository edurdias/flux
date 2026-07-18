"""T4 — End-to-end latency delta: the customer number (PLAN.md §2).

A deterministic SSE sidecar emits tokens on a fixed schedule with embedded
emit timestamps. Run 1 consumes it directly; run 2 routes the identical
schedule through a workflow that tees tokens into ``progress()`` and
consumes via Flux SSE. Both latency distributions measure from the same
sidecar emit instant, so their difference is exactly Flux's added overhead.

Gates (soft in ci): added p50 ≤ 30 ms, added p99 ≤ 150 ms.
"""

from __future__ import annotations

from fixtures.harness.consumer import percentile
from fixtures.harness.profile import params, profile_name, soft_gate
from fixtures.harness.report import write_run
from fixtures.harness.scenario import wait_all
from fixtures.harness.sidecar import TokenSidecar, consume_direct

P50_GATE_S = 0.030
P99_GATE_S = 0.150


def _flux_latencies(perf_env, namespace, sidecar, tokens, gap_ms, streams) -> list[float]:
    from fixtures.harness.consumer import StreamConsumer

    launched = []
    for _ in range(streams):
        run = perf_env.run_async(
            namespace,
            "perf_sidecar_stream",
            {"url": sidecar.url, "tokens": tokens, "gap_ms": gap_ms},
        )
        execution_id = run.get("execution_id") or run["id"]
        launched.append(
            (execution_id, StreamConsumer(perf_env.server_url, execution_id).start()),
        )
    consumers = [c for _, c in launched]
    wait_all(consumers, timeout=tokens * gap_ms / 1000 + 120)
    for c in consumers:
        c.stop()
    for execution_id, _ in launched:
        perf_env.wait_for_terminal(namespace, "perf_sidecar_stream", execution_id, timeout=120)
    return [f.t_recv - f.t_sent for c in consumers for f in c.frames if f.t_sent is not None]


def test_t4_latency_delta(perf_env, sidecar_workflow):
    namespace = sidecar_workflow
    p = params("t4")
    sidecar = TokenSidecar().start()
    try:
        results = {}
        for streams in (1, p["streams"]):
            direct = consume_direct(sidecar.url, p["tokens"], p["gap_ms"])
            flux = _flux_latencies(
                perf_env,
                namespace,
                sidecar,
                p["tokens"],
                p["gap_ms"],
                streams,
            )
            assert flux, f"no tokens arrived through Flux ({streams} streams)"
            results[f"{streams}_stream"] = {
                "direct_p50": percentile(direct, 0.50),
                "direct_p99": percentile(direct, 0.99),
                "flux_p50": percentile(flux, 0.50),
                "flux_p99": percentile(flux, 0.99),
                "delta_p50": percentile(flux, 0.50) - percentile(direct, 0.50),
                "delta_p99": percentile(flux, 0.99) - percentile(direct, 0.99),
                "tokens_direct": len(direct),
                "tokens_flux": len(flux),
            }
    finally:
        sidecar.stop()

    worst_p50 = max(r["delta_p50"] for r in results.values())
    worst_p99 = max(r["delta_p99"] for r in results.values())
    passed = soft_gate(
        worst_p50 <= P50_GATE_S and worst_p99 <= P99_GATE_S,
        f"delta p50 {worst_p50 * 1000:.1f}ms / p99 {worst_p99 * 1000:.1f}ms "
        f"exceeds {P50_GATE_S * 1000:.0f}/{P99_GATE_S * 1000:.0f}ms",
    )
    write_run(
        "T4",
        f"delta-{profile_name()}",
        {
            "gate": "added p50 <= 30ms, p99 <= 150ms (soft in ci)",
            "measured": (
                f"worst delta p50 {worst_p50 * 1000:.1f}ms, "
                f"p99 {worst_p99 * 1000:.1f}ms across 1 and {p['streams']} streams"
            ),
            "passed": passed,
            "sealed": False,
            "database": perf_env.database_url.split(":", 1)[0],
            "profile": profile_name(),
            "sidecar": "synthetic deterministic SSE emitter",
            "results": results,
        },
    )
