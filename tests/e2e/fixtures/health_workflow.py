"""Fixture for the worker self-health e2e scenario."""

from __future__ import annotations

import time

from flux import ExecutionContext, task, workflow


@task
async def block_loop_repeatedly(seconds_per_block: float, blocks: int) -> str:
    """Starve the worker's event loop in bursts.

    Each ``time.sleep`` blocks the loop long enough for a health probe to
    register a breach; the short awaits between bursts let the probe run.
    """
    import asyncio

    for _ in range(blocks):
        time.sleep(seconds_per_block)
        await asyncio.sleep(0.05)
    return "unblocked"


@workflow.with_options(runner="inprocess")
async def loop_blocker(ctx: ExecutionContext):
    """Runs ON the worker's event loop (inprocess) so the sync sleeps starve
    it — the subprocess default would isolate the damage."""
    return await block_loop_repeatedly(2.5, 5)


@workflow.with_options(runner="inprocess", affinity={"starve": "true"})
async def pinned_loop_blocker(ctx: ExecutionContext):
    """Same starvation, pinned by label so tests can starve one specific
    worker while the rest of the fleet stays healthy."""
    return await block_loop_repeatedly(2.5, 5)


@task
async def quick() -> str:
    return "healthy again"


@workflow
async def after_recovery(ctx: ExecutionContext):
    return await quick()
