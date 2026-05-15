"""Replay of TASK_FAILED events.

Failures are persisted through the same output_storage abstraction as
successes (``self.output_storage.store(task_id, ex)``), and the replay
short-circuit at task.py branches on event type: TASK_COMPLETED returns
the retrieved value, TASK_FAILED re-raises it.
"""

from __future__ import annotations

from flux import ExecutionContext, task as task_decorator, workflow
from flux.domain.events import ExecutionEventType
from flux.errors import ExecutionError
from flux.output_storage import OutputStorageReference


def test_failed_task_value_is_output_storage_reference(isolated_db):
    """A failure routes through output_storage so the configured backend
    persists the exception alongside successful outputs.

    The stored value is the same exception instance that gets raised,
    so replay re-raises identically — non-ExecutionError bodies are
    wrapped in ExecutionError(inner) by the engine and the wrapper is
    what gets stored.
    """

    class Boom(Exception):
        pass

    @task_decorator
    async def fails() -> str:
        raise Boom("nope")

    @workflow
    async def wf_value(ctx: ExecutionContext):
        return await fails()

    ctx = wf_value.run()
    assert ctx.has_failed
    failed = [e for e in ctx.events if e.type == ExecutionEventType.TASK_FAILED]
    assert len(failed) == 1
    assert isinstance(failed[0].value, OutputStorageReference)
    retrieved = fails.output_storage.retrieve(failed[0].value)
    assert isinstance(retrieved, ExecutionError)
    assert isinstance(retrieved.inner_exception, Boom)
    assert str(retrieved.inner_exception) == "nope"


def test_failed_task_replay_matches_original_run(isolated_db):
    """Replay symmetry: the workflow body must see the same exception on
    the original run and on every replay. Regression for the case where
    the task layer stored ``ex`` raw but raised the wrapped
    ``ExecutionError(ex)`` — replay would re-raise the raw inner and the
    workflow would catch a different type than on the first run.
    """

    class Boom(Exception):
        pass

    @task_decorator
    async def fails() -> str:
        raise Boom("nope")

    @workflow
    async def wf_replay(ctx: ExecutionContext):
        return await fails()

    first = wf_replay.run()
    assert first.has_failed
    assert isinstance(first.output, ExecutionError)
    assert isinstance(first.output.inner_exception, Boom)

    # Re-run with the same execution_id: the task call hits the replay
    # short-circuit, which retrieves the stored exception and re-raises
    # it. The workflow catches the same wrapped ExecutionError that it
    # caught on the first run.
    second = wf_replay.run(execution_id=first.execution_id)
    assert second.has_failed
    assert isinstance(second.output, ExecutionError)
    assert isinstance(second.output.inner_exception, Boom)


def test_completed_task_replay_still_returns_value(isolated_db):
    """Regression: TASK_COMPLETED replay still returns the stored output
    (the new branch must only re-raise on TASK_FAILED)."""

    @task_decorator
    async def t() -> str:
        return "first-result"

    @workflow
    async def wf_ok(ctx: ExecutionContext):
        return await t()

    first = wf_ok.run()
    assert first.has_succeeded
    assert first.output == "first-result"

    second = wf_ok.run(execution_id=first.execution_id)
    assert second.has_succeeded
    assert second.output == "first-result"
