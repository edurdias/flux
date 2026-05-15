"""Replay of TASK_FAILED events.

Failures are persisted through the same output_storage abstraction as
successes (``self.output_storage.store(task_id, ex)``), and the replay
short-circuit at task.py branches on event type: TASK_COMPLETED returns
the retrieved value, TASK_FAILED re-raises it.
"""

from __future__ import annotations

import asyncio

import pytest

from flux import ExecutionContext, task as task_decorator, workflow
from flux.domain.events import ExecutionEventType
from flux.output_storage import OutputStorageReference


def test_failed_task_value_is_output_storage_reference(isolated_db):
    """A failure routes through output_storage so the configured backend
    persists the exception alongside successful outputs."""

    class Boom(Exception):
        pass

    @task_decorator
    async def fails() -> str:
        raise Boom("nope")

    @workflow
    async def wf(ctx: ExecutionContext):
        return await fails()

    ctx = wf.run()
    assert ctx.has_failed
    failed = [e for e in ctx.events if e.type == ExecutionEventType.TASK_FAILED]
    assert len(failed) == 1
    assert isinstance(failed[0].value, OutputStorageReference)
    retrieved = fails.output_storage.retrieve(failed[0].value)
    assert isinstance(retrieved, Boom)
    assert str(retrieved) == "nope"


def test_failed_task_replay_reraises_stored_exception(isolated_db):
    """Direct test of the replay short-circuit: a TASK_FAILED event in the
    log must re-raise the stored exception on the next call, so the
    workflow body sees identical behavior on the original run and replay.
    """
    from flux.domain.events import ExecutionEvent

    class Boom(Exception):
        pass

    @task_decorator
    async def t() -> str:
        return "should-not-run"

    # Hand-craft a context with a pre-existing TASK_FAILED event for this task.
    ctx: ExecutionContext = ExecutionContext(
        workflow_id="wf-replay",
        workflow_namespace="default",
        workflow_name="replay",
        input=None,
    )

    # Mirror the engine's task_id derivation so the replay short-circuit
    # finds our injected event. See task.__call__ for the exact formula.
    from flux.utils import get_func_args, make_hashable

    args: tuple = ()
    kwargs: dict = {}
    task_args = get_func_args(t._func, args)
    task_id = (
        f"{t.name}_"
        f"{abs(hash((t.name, make_hashable(task_args), make_hashable(args), make_hashable(kwargs))))}"
    )

    boom = Boom("from-event-log")
    ctx.events.append(
        ExecutionEvent(
            type=ExecutionEventType.TASK_FAILED,
            source_id=task_id,
            name=t.name,
            value=t.output_storage.store(task_id, boom),
        ),
    )

    token = ExecutionContext.set(ctx)
    try:
        with pytest.raises(Boom) as exc_info:
            asyncio.run(t())
        assert str(exc_info.value) == "from-event-log"
    finally:
        ExecutionContext.reset(token)


def test_completed_task_replay_still_returns_value(isolated_db):
    """Regression: TASK_COMPLETED replay path still returns the stored
    output (the new branch must only re-raise on TASK_FAILED)."""
    from flux.domain.events import ExecutionEvent

    @task_decorator
    async def t() -> str:
        return "should-not-run-on-replay"

    ctx: ExecutionContext = ExecutionContext(
        workflow_id="wf-replay-ok",
        workflow_namespace="default",
        workflow_name="replay-ok",
        input=None,
    )

    from flux.utils import get_func_args, make_hashable

    args: tuple = ()
    kwargs: dict = {}
    task_args = get_func_args(t._func, args)
    task_id = (
        f"{t.name}_"
        f"{abs(hash((t.name, make_hashable(task_args), make_hashable(args), make_hashable(kwargs))))}"
    )

    ctx.events.append(
        ExecutionEvent(
            type=ExecutionEventType.TASK_COMPLETED,
            source_id=task_id,
            name=t.name,
            value=t.output_storage.store(task_id, "from-event-log"),
        ),
    )

    token = ExecutionContext.set(ctx)
    try:
        result = asyncio.run(t())
        assert result == "from-event-log"
    finally:
        ExecutionContext.reset(token)
