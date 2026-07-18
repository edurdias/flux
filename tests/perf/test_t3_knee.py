"""T3 — Server aggregate knee under synthetic load (PLAN.md §2).

Synthetic senders POST progress batches (≤50 frames, matching the real
worker flusher) directly to the server for executions that are held open by
real workflows with subscribed consumers. Offered load ramps in steps; the
knee is the last step with delivered/offered ≥ 0.99 and server CPU ≤ 70%.

Gate (soft in ci): knee ≥ 10k ev/s. On a 2-core shared runner the knee will
land wherever it lands — the number is the deliverable.
"""

from __future__ import annotations

from fixtures.harness.dbmeter import create_meter, diff
from fixtures.harness.loadgen import ProgressLoadGenerator
from fixtures.harness.profile import params, profile_name, soft_gate
from fixtures.harness.report import write_run
from fixtures.harness.sampler import ProcessSampler
from fixtures.harness.scenario import start_stream

KNEE_GATE = 10_000.0
RATIO_FLOOR = 0.99
CPU_CEILING = 70.0


def test_t3_server_knee(perf_env, stream_workflow):
    namespace, name = stream_workflow
    p = params("t3")
    total_load_s = len(p["steps"]) * p["step_seconds"]
    meter = create_meter(perf_env.database_url, perf_env.db_path)

    # Hold-open targets: real executions (0 frames, long sleep) with real
    # consumers — without a subscribed consumer the server discards ingest.
    targets = [
        start_stream(
            perf_env,
            namespace,
            name,
            frames=0,
            rate=0,
            hold_s=total_load_s + 60,
            start_delay=0.0,
        )
        for _ in range(p["targets"])
    ]
    execution_ids = [e for e, _ in targets]
    consumers = [c for _, c in targets]

    gen = ProgressLoadGenerator(perf_env.server_url, execution_ids)
    sampler = ProcessSampler({"server": perf_env.server_proc.pid}).start()
    before = meter.snapshot()

    steps = []
    try:
        for rate in p["steps"]:
            cpu_before = len(sampler.samples)
            step = gen.run_step(rate, p["step_seconds"])
            t0, t1 = step["window"]
            delivered = sum(sum(1 for f in c.frames if t0 <= f.t_recv <= t1) for c in consumers)
            step_cpu = [
                s.cpu_percent for s in sampler.samples[cpu_before:] if s.process == "server"
            ]
            step.update(
                {
                    "delivered": delivered,
                    "ratio": delivered / step["offered"] if step["offered"] else None,
                    "server_cpu_mean": (sum(step_cpu) / len(step_cpu)) if step_cpu else None,
                    "server_cpu_max": max(step_cpu, default=None),
                },
            )
            steps.append(step)
    finally:
        sampler.stop()
        after = meter.snapshot()
        for c in consumers:
            c.stop()
        for execution_id in execution_ids:
            try:
                perf_env.cancel(namespace, name, execution_id)
            except Exception:
                pass

    knee = None
    for step in steps:
        ratio_ok = step["ratio"] is not None and step["ratio"] >= RATIO_FLOOR
        cpu_ok = step["server_cpu_mean"] is None or step["server_cpu_mean"] <= CPU_CEILING
        if ratio_ok and cpu_ok:
            knee = step["rate_target"]
    past_knee = [s for s in steps if s["rate_target"] != knee]
    behavior = (
        "graceful (ratio degrades, no disconnects)"
        if all(c.error is None for c in consumers)
        else "collapse (consumer disconnects observed)"
    )

    # T0 under pressure: synthetic frames must not have leaked into the store.
    task_progress_rows = meter.count_event_type("TASK_PROGRESS")
    assert task_progress_rows == 0, f"{task_progress_rows} TASK_PROGRESS rows persisted under load"

    passed = soft_gate(
        knee is not None and knee >= KNEE_GATE,
        f"knee {knee} < {KNEE_GATE:.0f} ev/s",
    )
    write_run(
        "T3",
        f"knee-{profile_name()}",
        {
            "gate": f"knee >= {KNEE_GATE:.0f} ev/s (soft in ci); 0 progress rows persisted (hard)",
            "measured": (
                f"knee {knee or 'below first step'} ev/s; "
                f"ratios {[round(s['ratio'], 3) if s['ratio'] else None for s in steps]}; "
                f"past-knee behavior: {behavior}; progress rows persisted: 0"
            ),
            "passed": passed,
            "sealed": False,
            "database": perf_env.database_url.split(":", 1)[0],
            "profile": profile_name(),
            "steps": steps,
            "past_knee_steps": [s["rate_target"] for s in past_knee],
            "db_delta": diff(before, after),
            "process_usage": sampler.summary(),
        },
    )
