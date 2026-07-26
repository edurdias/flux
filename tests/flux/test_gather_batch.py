"""The batch contract: nothing outlives a batch that ended early.

``asyncio.gather`` leaves siblings running when one of them raises. For Flux
tasks that means a sibling can keep executing — and checkpointing — against an
execution the workflow has already recorded as ``PAUSED``. ``gather_batch``
closes that window; these tests hold it closed.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from flux import ExecutionContext, workflow
from flux._concurrency import gather_batch
from flux.errors import PauseRequested
from flux.task import task


# -- the helper in isolation ---------------------------------------------------


def test_returns_results_in_input_order():
    async def value(v: str, delay: float) -> str:
        await asyncio.sleep(delay)
        return v

    # Deliberately finishing out of order — results must still be in input order.
    results = asyncio.run(gather_batch([value("a", 0.03), value("b", 0.01), value("c", 0.02)]))
    assert results == ["a", "b", "c"]


@pytest.mark.parametrize(
    "raised",
    [PauseRequested(name="gate"), ValueError("boom"), asyncio.CancelledError()],
    ids=["pause", "error", "cancelled"],
)
def test_siblings_terminate_before_the_exception_propagates(raised):
    """Whatever ends the batch, no sibling is still running afterwards."""
    finished = {"slow": False}
    handle: dict[str, asyncio.Task] = {}
    state: dict[str, bool] = {}
    started = asyncio.Event()

    async def slow() -> str:
        current = asyncio.current_task()
        assert current is not None
        handle["task"] = current
        started.set()
        await asyncio.sleep(10)
        finished["slow"] = True
        return "too late"

    async def failing() -> str:
        await started.wait()  # make sure the sibling is genuinely in flight
        raise raised

    async def main():
        try:
            await gather_batch([slow(), failing()])
        except BaseException:
            # The contract: every task the batch spawned has already
            # terminated by the time the exception reaches the caller.
            # Plain asyncio.gather leaves this False.
            state["sibling_done"] = handle["task"].done()
            raise

    start = time.monotonic()
    with pytest.raises(type(raised)):
        asyncio.run(main())
    elapsed = time.monotonic() - start

    assert state["sibling_done"], "sibling was still running when the exception propagated"
    assert not finished["slow"], "sibling ran past the end of the batch"
    assert elapsed < 5, f"batch waited {elapsed:.1f}s on a cancellable sibling"


def test_sibling_exceptions_do_not_mask_the_original():
    """A sibling that raises while unwinding is consumed, not surfaced."""

    async def pausing() -> str:
        raise PauseRequested(name="gate")

    async def also_failing() -> str:
        await asyncio.sleep(0)
        raise ValueError("sibling failure")

    with pytest.raises(PauseRequested):
        asyncio.run(gather_batch([pausing(), also_failing()]))


# -- task.map() ----------------------------------------------------------------


def test_map_pause_stops_siblings():
    """A mapped call that pauses must not leave the rest of the map running.

    The pause is caught inside the workflow so the event loop outlives it — as
    it does on a worker. Asserting from outside ``wf.run()`` would prove
    nothing: ``asyncio.run`` cancels stragglers at loop close and would mask an
    orphan.
    """
    finished = {"sibling": False}

    @task
    async def gate_or_sleep(value: int) -> int:
        if value == 0:
            raise PauseRequested(name="gate")
        await asyncio.sleep(0.2)
        finished["sibling"] = True
        return value

    @workflow
    async def wf(ctx: ExecutionContext):
        try:
            await gate_or_sleep.map([0, 1])
        except PauseRequested:
            pass
        # Longer than the sibling's own runtime: an orphan would finish here.
        await asyncio.sleep(0.5)
        return finished["sibling"]

    ctx = wf.run()
    assert ctx.has_succeeded, ctx.output
    assert ctx.output is False, "mapped sibling ran on past the pause"


def test_map_pause_surfaces_promptly():
    """The pause itself still reaches the workflow without waiting on siblings."""
    finished = {"sibling": False}

    @task
    async def gate_or_sleep(value: int) -> int:
        if value == 0:
            raise PauseRequested(name="gate")
        await asyncio.sleep(10)
        finished["sibling"] = True
        return value

    @workflow
    async def wf(ctx: ExecutionContext):
        return await gate_or_sleep.map([0, 1])

    start = time.monotonic()
    ctx = wf.run()
    elapsed = time.monotonic() - start

    assert ctx.is_paused
    assert not finished["sibling"]
    assert elapsed < 5, f"pause was delayed {elapsed:.1f}s by a mapped sibling"


def test_map_still_returns_results_in_order():
    @task
    async def double(value: int) -> int:
        await asyncio.sleep(0.01 * (3 - value))
        return value * 2

    @workflow
    async def wf(ctx: ExecutionContext):
        return await double.map([1, 2, 3])

    ctx = wf.run()
    assert ctx.has_succeeded, ctx.output
    assert ctx.output == [2, 4, 6]
