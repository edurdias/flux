"""Fixtures for the worker runtime-control e2e scenario.

Both workflows pin to workers labeled ``pausable=true`` so the tests can
start a dedicated worker, pause/cancel it, and prove dispatch behavior
without touching the session worker.
"""

from __future__ import annotations

import asyncio

from flux import ExecutionContext, task, workflow


@task
async def mark_started() -> str:
    return "started"


@task
async def pinned_sleep(seconds: int) -> str:
    await asyncio.sleep(seconds)
    return f"slept {seconds}"


@workflow.with_options(affinity={"pausable": "true"})
async def pause_pinned_slow(ctx: ExecutionContext):
    # A quick first task so the RUNNING transition is checkpointed right
    # away — the long sleep alone would leave the server at CLAIMED until
    # its first (post-sleep) checkpoint.
    await mark_started()
    return await pinned_sleep(ctx.input or 60)


@workflow.with_options(affinity={"pausable": "true"})
async def pause_pinned_quick(ctx: ExecutionContext):
    return "ran after resume"
