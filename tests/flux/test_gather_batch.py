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


def test_raising_iterable_schedules_nothing():
    """An input iterable that raises part-way must not leave members running.

    Scheduling inside the comprehension would create the exact orphan the helper
    exists to prevent: the members the generator already yielded become live
    detached tasks whose side effects land after the batch died.
    """
    ran = {"n": 0}

    async def worker() -> int:
        await asyncio.sleep(0.05)
        ran["n"] += 1  # a side effect landing after the batch failed
        return 1

    def raising_gen():
        yield worker()
        yield worker()
        raise ValueError("iterable blew up part-way")

    async def main():
        with pytest.raises(ValueError):
            await gather_batch(raising_gen())
        leftover = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        await asyncio.sleep(0.2)  # an orphan would finish during this window
        return len(leftover), ran["n"]

    leftover, side_effects = asyncio.run(main())
    assert leftover == 0, "the raising iterable left scheduled tasks behind"
    assert side_effects == 0, "an orphaned member ran after the batch failed"


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


def test_map_pause_drains_siblings_before_propagating():
    """A sibling that can finish inside the drain window is allowed to finish.

    Durability over promptness: killing a nearly-done sibling would leave its
    side effect applied but unrecorded, so replay would repeat it. Draining
    records it instead. The pause is caught inside the workflow so the loop
    outlives it, as it does on a worker.
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
            # The sibling must already be done: gather_batch does not re-raise
            # until every member has terminated.
            return finished["sibling"]
        return None

    ctx = wf.run()
    assert ctx.has_succeeded, ctx.output
    assert ctx.output is True, "sibling was killed instead of drained"


def test_straggler_past_the_bound_is_cancelled():
    """The drain is bounded -- a sibling that outlives it is still cancelled."""
    finished = {"slow": False}

    async def slow() -> str:
        await asyncio.sleep(10)
        finished["slow"] = True
        return "too late"

    async def failing() -> str:
        await asyncio.sleep(0)
        raise ValueError("boom")

    start = time.monotonic()
    with pytest.raises(ValueError):
        asyncio.run(gather_batch([slow(), failing()], drain_timeout=0.05))
    elapsed = time.monotonic() - start

    assert not finished["slow"], "straggler was not cancelled at the bound"
    assert elapsed < 5, f"bounded drain took {elapsed:.1f}s"


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


def test_map_drains_siblings_on_an_ordinary_failure_too():
    """The drain is deliberately not pause-specific.

    `flux/workflow.py` records `ctx.fail(...)` and checkpoints in the same
    `finally` as the pause path, so a sibling outliving an ordinary failure
    writes to an already-FAILED execution -- the same defect a pause causes.
    This pins that decision: the drain applies uniformly, so a sibling that can
    finish does -- which is what lets its rollback and terminal event run. Only
    stragglers past the bound are cancelled.
    """
    finished = {"sibling": False}

    @task
    async def fail_or_sleep(value: int) -> int:
        if value == 0:
            raise ValueError("boom")
        await asyncio.sleep(0.2)
        finished["sibling"] = True
        return value

    @workflow
    async def wf(ctx: ExecutionContext):
        try:
            await fail_or_sleep.map([0, 1])
        except Exception:
            return finished["sibling"]
        return None

    ctx = wf.run()
    assert ctx.has_succeeded, ctx.output
    assert ctx.output is True, "sibling was killed instead of drained on failure"


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
