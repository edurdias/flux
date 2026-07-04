"""Fixtures for the runner e2e matrix.

The worker's default runner is subprocess, so every other e2e fixture
already exercises the subprocess path; these cover the runner-specific
scenarios — pinning, hard crashes, and crash + durability interplay.
"""

from __future__ import annotations

import os
from pathlib import Path

from flux import ExecutionContext, task, workflow


@task
async def add_one(x: int) -> int:
    return x + 1


@workflow.with_options(runner="inprocess")
async def inprocess_pinned(ctx: ExecutionContext[int]):
    """Runs on the worker's event loop even though the default is subprocess."""
    return await add_one(ctx.input)


@workflow.with_options(runner="subprocess")
async def subprocess_pinned(ctx: ExecutionContext[int]):
    return await add_one(ctx.input)


@task
async def record_completed(base_dir: str) -> str:
    """Completes (and is checkpointed) before the crash task runs.

    Appends one line per *execution* — a replayed dispatch short-circuits
    this task from persisted events, so the file must end up with exactly
    one line no matter how many times the workflow is dispatched.
    """
    path = Path(base_dir) / "completed"
    with open(path, "a") as f:
        f.write("ran\n")
    return "completed"


@task
async def attempt_and_maybe_crash(base_dir: str) -> str:
    """Dies before completing on the first dispatch, so it is never
    checkpointed and re-runs (appending again) on re-dispatch."""
    path = Path(base_dir) / "attempts"
    with open(path, "a") as f:
        f.write("attempt\n")
    if len(path.read_text().splitlines()) < 2:
        # Hard process death: no exception, no result frame — the parent
        # worker must detect the crash and release the claim.
        os._exit(9)
    return "survived"


@workflow
async def durable_crash_once(ctx: ExecutionContext[str]):
    """Crashes the child on the first dispatch, succeeds on re-dispatch."""
    await record_completed(ctx.input)
    return await attempt_and_maybe_crash(ctx.input)


@workflow.with_options(durability="transient")
async def transient_crash(ctx: ExecutionContext[str]):
    """A transient execution that dies mid-run must fail terminally."""
    os._exit(9)
