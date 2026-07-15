from __future__ import annotations

import asyncio

import pytest

from flux import ExecutionContext, workflow
from flux.errors import PauseRequested
from flux.task import task
from flux.tasks import parallel


@task
async def echo(value: str) -> str:
    return value


@task
async def boom(value: str) -> str:
    raise ValueError(f"boom: {value}")


def test_results_in_input_order():
    @workflow
    async def wf(ctx: ExecutionContext):
        return await parallel(echo("a"), echo("b"), echo("c"))

    ctx = wf.run()
    assert ctx.has_succeeded, ctx.output
    assert ctx.output == ["a", "b", "c"]


def test_raise_on_error_default_fails_batch():
    @workflow
    async def wf(ctx: ExecutionContext):
        return await parallel(echo("a"), boom("b"), echo("c"))

    ctx = wf.run()
    assert ctx.has_failed


def test_error_drop_keeps_batch_alive():
    @workflow
    async def wf(ctx: ExecutionContext):
        return await parallel(
            echo("a"),
            boom("b"),
            echo("c"),
            raise_on_error=False,
        )

    ctx = wf.run()
    assert ctx.has_succeeded, ctx.output
    assert ctx.output == ["a", None, "c"]


def test_pause_propagates_even_with_error_drop():
    @task
    async def pausing() -> str:
        raise PauseRequested(name="gate")

    @workflow
    async def wf(ctx: ExecutionContext):
        return await parallel(echo("a"), pausing(), raise_on_error=False)

    ctx = wf.run()
    assert ctx.is_paused
    assert not ctx.has_finished


def test_pause_is_not_delayed_by_running_siblings():
    """A pause must surface as soon as it happens — not after every other
    item in the batch completes."""
    import time

    @task
    async def pausing() -> str:
        raise PauseRequested(name="gate")

    @task
    async def slow() -> str:
        await asyncio.sleep(10)
        return "too late"

    @workflow
    async def wf(ctx: ExecutionContext):
        return await parallel(slow(), pausing(), raise_on_error=False)

    start = time.monotonic()
    ctx = wf.run()
    elapsed = time.monotonic() - start

    assert ctx.is_paused
    assert elapsed < 5, f"pause was delayed {elapsed:.1f}s by a running sibling"


def test_siblings_cancelled_on_fail_fast():
    """When the batch fails, still-running siblings are cancelled instead of
    running on (and emitting events) past the failure."""
    finished = {"slow": False}

    @task
    async def slow() -> str:
        await asyncio.sleep(10)
        finished["slow"] = True
        return "too late"

    @workflow
    async def wf(ctx: ExecutionContext):
        return await parallel(slow(), boom("b"))

    import time

    start = time.monotonic()
    ctx = wf.run()
    elapsed = time.monotonic() - start

    assert ctx.has_failed
    assert not finished["slow"]
    assert elapsed < 5, f"failure waited {elapsed:.1f}s on a running sibling"


def test_max_concurrent_bounds_in_flight():
    in_flight = {"now": 0, "peak": 0}

    @task
    async def tracked(i: int) -> int:
        in_flight["now"] += 1
        in_flight["peak"] = max(in_flight["peak"], in_flight["now"])
        await asyncio.sleep(0.01)
        in_flight["now"] -= 1
        return i

    @workflow
    async def wf(ctx: ExecutionContext):
        return await parallel(*[tracked(i) for i in range(10)], max_concurrent=2)

    ctx = wf.run()
    assert ctx.has_succeeded, ctx.output
    assert ctx.output == list(range(10))
    assert in_flight["peak"] <= 2


def test_unbounded_by_default():
    in_flight = {"now": 0, "peak": 0}

    @task
    async def tracked(i: int) -> int:
        in_flight["now"] += 1
        in_flight["peak"] = max(in_flight["peak"], in_flight["now"])
        await asyncio.sleep(0.01)
        in_flight["now"] -= 1
        return i

    @workflow
    async def wf(ctx: ExecutionContext):
        return await parallel(*[tracked(i) for i in range(5)])

    ctx = wf.run()
    assert ctx.has_succeeded, ctx.output
    assert in_flight["peak"] == 5


def test_invalid_max_concurrent_rejected():
    @workflow
    async def wf(ctx: ExecutionContext):
        return await parallel(echo("a"), max_concurrent=0)

    ctx = wf.run()
    assert ctx.has_failed


def test_error_drop_with_max_concurrent():
    @workflow
    async def wf(ctx: ExecutionContext):
        return await parallel(
            *[boom(str(i)) if i % 2 else echo(str(i)) for i in range(6)],
            max_concurrent=2,
            raise_on_error=False,
        )

    ctx = wf.run()
    assert ctx.has_succeeded, ctx.output
    assert ctx.output == ["0", None, "2", None, "4", None]


@pytest.mark.parametrize("raise_on_error", [True, False])
def test_staged_per_item_idiom(raise_on_error: bool):
    """The documented shape for staged fan-out: chain stages in a plain
    async function, fan out with parallel."""

    @task
    async def fetch(doc: str) -> str:
        return f"content({doc})"

    @task
    async def summarize(content: str) -> str:
        return f"summary({content})"

    async def process(doc: str) -> str:
        return await summarize(await fetch(doc))

    @workflow
    async def wf(ctx: ExecutionContext):
        return await parallel(
            *[process(d) for d in ["d1", "d2"]],
            raise_on_error=raise_on_error,
        )

    ctx = wf.run()
    assert ctx.has_succeeded, ctx.output
    assert ctx.output == ["summary(content(d1))", "summary(content(d2))"]
