"""Synthetic progress-streaming workflow for the perf suite.

Emits ``frames`` progress events at ``rate`` events/s, each padded to
``size`` bytes. Every frame carries ``{"i": seq, "t": <wall ts at send>}``
so consumers can compute loss (by sequence) and end-to-end latency
(sender-stamped, per PLAN.md — the server re-stamps event time at ingest,
which would hide the transport).

``start_delay`` holds the first frame back so a detached SSE consumer can
subscribe before frame 0: frames posted before the server-side buffer exists
are discarded by design (ephemeral), which is correct behavior but ruins
frame accounting.

``jitter`` (0..1) randomizes each inter-frame gap by ±jitter fraction.
"""

from __future__ import annotations

import asyncio
import random
import time

from flux import ExecutionContext, task, workflow
from flux.tasks import progress


@task
async def emit_frames(
    frames: int,
    rate: float,
    size: int,
    start_delay: float,
    jitter: float,
) -> int:
    if start_delay > 0:
        await asyncio.sleep(start_delay)
    pad = "x" * size
    interval = 1.0 / rate if rate > 0 else 0.0
    next_at = time.monotonic()
    for i in range(frames):
        await progress({"i": i, "t": time.time(), "pad": pad})
        if interval:
            gap = interval
            if jitter:
                gap *= 1.0 + random.uniform(-jitter, jitter)
            next_at += gap
            delay = next_at - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            else:
                # Fell behind (emit slower than requested rate): resync
                # instead of bursting to catch up.
                next_at = time.monotonic()
    return frames


@workflow
async def perf_stream(ctx: ExecutionContext):
    params = ctx.input or {}
    sent = await emit_frames(
        frames=int(params.get("frames", 100)),
        rate=float(params.get("rate", 100.0)),
        size=int(params.get("size", 150)),
        start_delay=float(params.get("start_delay", 0.0)),
        jitter=float(params.get("jitter", 0.0)),
    )
    return {"sent": sent}
