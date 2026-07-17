"""T0 — Persistence verification (PLAN.md §2). Runs first; gates everything.

Verifies, against a live server + worker over the real distributed path, that
``progress()`` frames are delivered to an SSE consumer but never persisted:

1. the consumer demonstrably receives progress frames (the stream works);
2. zero TASK_PROGRESS rows exist anywhere in execution_events;
3. the persisted event footprint of an execution is independent of how many
   progress frames it emitted (differential: 100 vs 5,000 frames);
4. the detailed status DTO carries no TASK_PROGRESS events.

Replay-skip is already unit-covered inline (tests/flux/test_progress_durability.py);
distributed reclaim is out of T0's scope per PLAN.md changelog-6.
"""

from __future__ import annotations

import json
import os

from fixtures.harness.consumer import StreamConsumer
from fixtures.harness.dbmeter import SqliteDbMeter, diff
from fixtures.harness.report import write_run

FRAMES_SMALL = 100
FRAMES_LARGE = int(os.environ.get("FLUX_PERF_T0_FRAMES", "5000"))
RATE = 200.0
FRAME_PAD_BYTES = 150
# Frames emitted before a consumer subscribes are discarded by design; hold
# the first frame back long enough for claim + module load + SSE attach.
START_DELAY_S = 2.0


def _run_streaming(perf_env, namespace: str, name: str, frames: int) -> dict:
    """Run one perf_stream execution with an attached consumer; return facts."""
    run = perf_env.run_async(
        namespace,
        name,
        {
            "frames": frames,
            "rate": RATE,
            "size": FRAME_PAD_BYTES,
            "start_delay": START_DELAY_S,
        },
    )
    execution_id = run.get("execution_id") or run["id"]

    consumer = StreamConsumer(perf_env.server_url, execution_id).start()
    timeout = START_DELAY_S + frames / RATE + 90
    consumer.wait(timeout)
    consumer.stop()
    if consumer.error:
        raise AssertionError(
            f"Consumer for {execution_id} died: {consumer.error!r}",
        ) from consumer.error

    final = perf_env.wait_for_terminal(namespace, name, execution_id, timeout=60)
    assert final["state"] == "COMPLETED", (
        f"Execution {execution_id} ended {final['state']}: {json.dumps(final)[:500]}"
    )
    return {
        "execution_id": execution_id,
        "frames_offered": frames,
        "consumer": consumer.stats().as_dict(),
    }


def test_t0_progress_never_persisted(perf_env, stream_workflow):
    namespace, name = stream_workflow
    meter = SqliteDbMeter(perf_env.db_path)
    rtt = perf_env.measure_http_rtt()

    before_small = meter.snapshot()
    small = _run_streaming(perf_env, namespace, name, FRAMES_SMALL)
    after_small = meter.snapshot()

    before_large = meter.snapshot()
    large = _run_streaming(perf_env, namespace, name, FRAMES_LARGE)
    after_large = meter.snapshot()

    # 1. The stream demonstrably works: both consumers saw progress frames.
    assert small["consumer"]["delivered"] > 0, "small run delivered no frames"
    assert large["consumer"]["delivered"] > 0, "large run delivered no frames"

    # 2. Nothing progress-shaped ever reached the event store.
    task_progress_rows = meter.count_event_type("TASK_PROGRESS")
    assert task_progress_rows == 0, (
        f"{task_progress_rows} TASK_PROGRESS rows persisted in execution_events"
    )

    # 3. Persisted footprint is independent of frame volume: both executions
    # ran the identical workflow shape, so their event rows must match
    # exactly, type by type.
    rows_small = meter.execution_event_rows(small["execution_id"])
    rows_large = meter.execution_event_rows(large["execution_id"])
    assert rows_small == rows_large, (
        f"Persisted event footprint varies with progress volume:\n"
        f"  {FRAMES_SMALL} frames -> {rows_small}\n"
        f"  {FRAMES_LARGE} frames -> {rows_large}"
    )

    # 4. The detailed DTO exposes no progress events either.
    detailed = perf_env.status(
        namespace,
        name,
        large["execution_id"],
        detailed=True,
    )
    dto_types = [e.get("type") for e in detailed.get("events", [])]
    assert "TASK_PROGRESS" not in dto_types, (
        f"detailed DTO leaked TASK_PROGRESS events: {dto_types}"
    )

    write_run(
        "T0",
        "persistence",
        {
            "gate": "0 TASK_PROGRESS rows; event footprint independent of frame count",
            "measured": (
                f"TASK_PROGRESS rows={task_progress_rows}; "
                f"footprint {FRAMES_SMALL}f == {FRAMES_LARGE}f "
                f"({sum(rows_small.values())} rows each); "
                f"delivered {small['consumer']['delivered']}/{FRAMES_SMALL} "
                f"and {large['consumer']['delivered']}/{FRAMES_LARGE}"
            ),
            "passed": True,
            "sealed": False,  # default subprocess runner; sealed variants start at T1
            "http_rtt_s": rtt,
            "rate_events_per_s": RATE,
            "frame_pad_bytes": FRAME_PAD_BYTES,
            "runs": {"small": small, "large": large},
            "db_delta_small": diff(before_small, after_small),
            "db_delta_large": diff(before_large, after_large),
        },
    )
