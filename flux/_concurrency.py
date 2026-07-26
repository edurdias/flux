"""Batch execution discipline for awaitables that can suspend the workflow.

``asyncio.gather`` propagates the first exception but leaves its siblings
running — it only cancels children when the *gather itself* is cancelled. For
ordinary application code that is harmless. For Flux tasks it is not: a task
appends its terminal event and calls ``ctx.checkpoint()`` when it finishes
(``flux/task.py``), so a sibling still running when a ``PauseRequested``
propagates keeps executing — and checkpointing — against an execution the
workflow has already recorded as ``PAUSED``. Depending on timing the write is
accepted (a task visibly completes *during* the suspension), rejected by the
stale-state guard and lost (so replay re-runs the side effect), or torn when a
runner child exits mid-flight.

``gather_batch`` closes that window: whatever ends the batch — a pause, a
failure, or cancellation — every sibling has *terminated* before the exception
propagates.

The contract is deliberately "cancel, then wait", not "wait":

* A sibling that can be interrupted stops at its next await, so a pause still
  surfaces promptly (``tests/flux/tasks/test_parallel.py::
  test_pause_is_not_delayed_by_running_siblings`` guards this).
* A sibling that cannot be interrupted — a synchronous task body — runs to
  completion regardless. Because the terminal event is appended *before* the
  checkpoint is awaited, its result is still in ``ctx.events`` and is flushed
  by the pause's own checkpoint.

So siblings finish if they cannot be stopped and stop if they can, and either
way nothing outlives the batch.

Two consequences callers should know about:

* **Cancellation is not pause-specific.** Any batch-ending exception cancels the
  siblings, because ``flux/workflow.py`` records ``ctx.fail(...)`` and
  checkpoints in the same ``finally`` as the pause path — a sibling outliving an
  ordinary failure writes to an already-FAILED execution, the same defect.
* **Batch siblings are at-least-once.** A sibling cancelled mid-body after its
  side effect but before its terminal event is appended leaves no record, so
  replay re-runs it, and its rollback does not fire. Prefer idempotent bodies
  for work in a batch alongside anything that can pause or fail.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Iterable
from typing import Any


async def gather_batch(awaitables: Iterable[Awaitable[Any]]) -> list[Any]:
    """Run ``awaitables`` concurrently, returning results in input order.

    Behaves like ``asyncio.gather`` on the happy path. When any of them raises,
    the remaining siblings are cancelled and awaited to termination before the
    exception propagates, so none of them can outlive the batch.

    Sibling exceptions raised during that unwind are consumed rather than
    re-raised: the original exception is what the caller must see, and
    retrieving the rest keeps asyncio from logging "Task exception was never
    retrieved" for them.
    """
    # Materialise before scheduling anything. An iterable that raises part-way
    # (``task.map(row for row in stream_rows())``) must not leave the members it
    # already yielded running detached — that is the very orphan this helper
    # exists to prevent, and scheduling inside the comprehension would create it.
    materialised: list[Awaitable[Any]] = []
    try:
        for awaitable in awaitables:
            materialised.append(awaitable)
    except BaseException:
        # Close what the iterable already produced so nothing is left as an
        # un-awaited coroutine, then let the iterable's own error propagate.
        for awaitable in materialised:
            close = getattr(awaitable, "close", None)
            if close is not None:
                close()
        raise

    tasks: list[asyncio.Task] = [asyncio.ensure_future(a) for a in materialised]
    try:
        return list(await asyncio.gather(*tasks))
    except BaseException:
        for task in tasks:
            if not task.done():
                task.cancel()
        # return_exceptions=True both waits for every task to finish unwinding
        # and marks their exceptions retrieved.
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
