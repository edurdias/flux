from __future__ import annotations

from pathlib import Path

from flux import ExecutionContext
from flux.task import task
from flux.tasks import call, sleep
from flux.workflow import workflow


@task
async def double(n: int):
    return n * 2


@task
async def boom():
    raise ValueError("boom")


@task.with_options(retry_max_attempts=3, retry_delay=1)
async def flaky_once(path: str):
    """Fails on the first attempt, succeeds on retry — driven by a marker file."""
    marker = Path(path)
    if not marker.exists():
        marker.write_text("attempted")
        raise RuntimeError("first attempt fails")
    return "recovered"


@task.with_options(requires_approval=True)
async def gated(n: int):
    return n


@workflow.with_options(durability="transient")
async def transient_double(ctx: ExecutionContext[int]):
    a = await double(ctx.input)
    return await double(a)


@workflow.with_options(durability="transient")
async def transient_pause_attempt(ctx: ExecutionContext[str]):
    from flux.tasks import pause

    await pause("never-allowed")
    return "unreachable"


@workflow.with_options(durability="transient")
async def transient_failing(ctx: ExecutionContext[str]):
    await boom()
    return "unreachable"


@workflow.with_options(durability="transient")
async def transient_retry(ctx: ExecutionContext[str]):
    return await flaky_once(ctx.input)


@workflow.with_options(durability="transient")
async def transient_approval_attempt(ctx: ExecutionContext[int]):
    return await gated(ctx.input)


@workflow.with_options(durability="transient")
async def transient_slow(ctx: ExecutionContext[str]):
    # Distinct durations per iteration: identical args would give identical
    # deterministic task ids, and every call after the first would replay
    # from the in-memory event log instead of sleeping.
    for i in range(60):
        await sleep(1 + i / 1000)
    return "done"


@workflow
async def durable_sibling(ctx: ExecutionContext[int]):
    """Durable control: registered from the same file, must keep TASK events."""
    return await double(ctx.input)


@workflow
async def durable_calls_transient(ctx: ExecutionContext[int]):
    """Mesh hop via server relay: a string reference always dispatches."""
    return await call("transient_double", ctx.input)


@workflow
async def durable_calls_transient_fast(ctx: ExecutionContext[int]):
    """Mesh hop via the same-worker fast path: a transient workflow *object*
    executes in-process on the worker — no dispatch, no execution row."""
    return await call(transient_double, ctx.input)
