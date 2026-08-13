"""Batch execution discipline for awaitables that can suspend the workflow.

``asyncio.gather`` propagates the first exception but leaves its siblings
running — it only cancels children when the *gather itself* is cancelled. For
ordinary application code that is harmless. For Flux tasks it is not: a task
appends its terminal event and calls ``ctx.checkpoint()`` when it finishes
(``flux/task.py``), so a sibling still running when the batch ends keeps
executing — and checkpointing — against an execution the workflow has already
recorded as ``PAUSED`` or ``FAILED``.

``gather_batch`` closes that window. The design decision it encodes is
**durability and consistency over promptness**: when a batch ends early, its
siblings are given a bounded chance to *finish* before they are cancelled.

Why finishing beats killing: cancellation does not prevent a sibling's side
effect, it only interrupts the *record* of it. A cancelled task appends an
audit-only ``TASK_CANCELLED`` event and runs its declared rollback (issue
#149, ``flux/task.py::__handle_cancellation``), but the rollback compensates
rather than completes: the task's real outcome is lost and replay re-runs the
body on resume. Letting a sibling finish instead records its actual result,
runs its rollback only if it genuinely fails, and lets replay short-circuit
on resume.

The contract, in order:

1. **Drain** — wait up to ``drain_timeout`` for siblings to finish on their own.
   Most finish in milliseconds, so this is usually free.
2. **Cancel** — stragglers past the bound are cancelled, then awaited to
   termination. The bound is what keeps one wedged sibling from stranding the
   execution: an unbounded wait eventually loses its claim and replays from
   scratch, which is worse for durability than cancelling.
3. **Re-raise** — only once every sibling has terminated.

``CancelledError`` skips the drain. Being cancelled means "stop now" — usually a
worker drain deadline or an operator cancel that has its own clock — and
honoring that promptly is itself part of cancelling gracefully.

**Tasks in a batch must be idempotent.** A straggler cancelled past the bound
re-executes on resume. That expectation already follows from Flux's replay
model; batching makes it load-bearing.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Iterable
from typing import Any

# Small enough that a pause still surfaces promptly, large enough that ordinary
# task bodies (a file write, an HTTP call) finish inside it.
DEFAULT_DRAIN_TIMEOUT = 2.0

# Bound on a cancelled task's rollback (flux/task.py::__handle_cancellation).
# The rollback runs shielded so a second cancel (a worker drain escalating)
# cannot interrupt the compensation, which means this deadline is the only
# thing keeping a wedged rollback from stranding the drain. Sized above the
# drain timeout: compensation is allowed to outlast the patience we extend to
# the original body.
CANCELLED_ROLLBACK_TIMEOUT = 10.0

# Bound on persisting a cancelled workflow's terminal state
# (flux/workflow.py). That checkpoint runs in the `finally` of a coroutine
# already unwinding from a delivered CancelledError, so a *second* cancel — and
# the dispatcher re-sends a cancellation every cycle while the row is still
# CANCELLING — interrupts the await and the CANCELLED state is never written.
# The row then stays CANCELLING, which is re-dispatched, which cancels again
# (issue #189). Shielded for the same reason as the rollback above, and bounded
# for the same reason: a wedged checkpoint must not strand a worker drain.
CANCELLED_CHECKPOINT_TIMEOUT = 10.0


async def gather_batch(
    awaitables: Iterable[Awaitable[Any]],
    *,
    drain_timeout: float = DEFAULT_DRAIN_TIMEOUT,
) -> list[Any]:
    """Run ``awaitables`` concurrently, returning results in input order.

    Behaves like ``asyncio.gather`` on the happy path. When any of them raises,
    the remaining siblings are drained for up to ``drain_timeout`` seconds, then
    cancelled and awaited to termination, before the exception propagates — so
    none of them can outlive the batch. ``CancelledError`` skips the drain.

    Sibling exceptions raised during that settling are consumed rather than
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
    except asyncio.CancelledError:
        # Told to stop now — skip the drain and settle immediately.
        await _settle(tasks)
        raise
    except BaseException:
        await _drain_then_settle(tasks, drain_timeout)
        raise


async def _drain_then_settle(tasks: list[asyncio.Task], drain_timeout: float) -> None:
    """Give unfinished siblings ``drain_timeout`` to complete, then settle."""
    pending = [task for task in tasks if not task.done()]
    try:
        if pending and drain_timeout > 0:
            # asyncio.wait never raises for a task's own exception; whatever the
            # drained siblings raise is retrieved by _settle below.
            await asyncio.wait(pending, timeout=drain_timeout)
    finally:
        # Best-effort even if the drain itself is interrupted: a second
        # cancellation arriving here still leaves no sibling running.
        await _settle(tasks)


async def _settle(tasks: list[asyncio.Task]) -> None:
    """Cancel anything still running and await every task to termination."""
    for task in tasks:
        if not task.done():
            task.cancel()
    # return_exceptions=True both waits for every task to finish unwinding and
    # marks their exceptions retrieved.
    await asyncio.gather(*tasks, return_exceptions=True)
