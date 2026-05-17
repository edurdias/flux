"""Regression tests for task retry backoff and per-retry timeout."""

from __future__ import annotations

import asyncio
import time

from flux import ExecutionContext
from flux.domain.events import ExecutionEventType
from flux.task import task
from flux.workflow import workflow


def test_retry_backoff_compounds(monkeypatch):
    """retry_backoff must actually compound the delay across retries.

    Previously current_delay was reset to retry_delay every iteration, so the
    backoff multiplier never affected runtime behaviour.
    """
    real_sleep = asyncio.sleep

    async def fast_sleep(_delay, *args, **kwargs):
        # Keep the test fast; the assertion is on the recorded event metadata.
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", fast_sleep)

    calls = {"n": 0}

    @task.with_options(retry_max_attempts=4, retry_delay=1, retry_backoff=3)
    async def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ValueError("boom")
        return "ok"

    @workflow
    async def backoff_wf(ctx: ExecutionContext):
        return await flaky()

    ctx = backoff_wf.run()

    assert ctx.has_finished and ctx.has_succeeded
    assert ctx.output == "ok"

    retry_delays = [
        e.value["current_delay"]
        for e in ctx.events
        if e.type == ExecutionEventType.TASK_RETRY_STARTED
    ]
    # First retry waits retry_delay, second waits retry_delay * retry_backoff.
    assert retry_delays == [1, 3]


def test_timeout_applies_to_retry_attempts():
    """A task that hangs on a retry must be killed by its configured timeout
    rather than hanging forever.
    """
    calls = {"n": 0}

    @task.with_options(retry_max_attempts=3, retry_delay=1, retry_backoff=1, timeout=1)
    async def hangs_on_retry():
        calls["n"] += 1
        if calls["n"] == 1:
            raise ValueError("first attempt fails fast")
        # Retry attempts hang — the timeout must cut them off.
        await asyncio.sleep(3600)

    @workflow
    async def timeout_wf(ctx: ExecutionContext):
        return await hangs_on_retry()

    start = time.monotonic()
    ctx = timeout_wf.run()
    elapsed = time.monotonic() - start

    assert ctx.has_finished and ctx.has_failed
    # Without the fix the first retry would hang indefinitely.
    assert elapsed < 30
