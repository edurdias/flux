"""Replay of TASK_FAILED events.

Failures are persisted through the same output_storage abstraction as
successes (``self.output_storage.store(task_id, ex)``), and the replay
short-circuit at task.py branches on event type: TASK_COMPLETED returns
the retrieved value, TASK_FAILED re-raises it.

Tests use builtin exception types (``ValueError``) deliberately: a
function-local exception class does not survive a dill pickle/unpickle
round-trip with a stable class identity, so ``isinstance`` against the
test-local class would fail after the event log reloads it.
"""

from __future__ import annotations

from flux import ExecutionContext, task as task_decorator, workflow
from flux.domain.events import ExecutionEventType
from flux.errors import ExecutionError
from flux.output_storage import OutputStorageReference


def test_failed_task_value_is_output_storage_reference(isolated_db):
    """A failure routes through output_storage so the configured backend
    persists the exception alongside successful outputs.

    The stored value is the same exception instance that gets raised, so
    replay re-raises identically — non-ExecutionError bodies are wrapped
    in ExecutionError(inner) by the engine and the wrapper is stored.
    """

    @task_decorator
    async def fails() -> str:
        raise ValueError("nope")

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
    assert isinstance(retrieved.inner_exception, ValueError)
    assert str(retrieved.inner_exception) == "nope"


def test_failed_workflow_reload_preserves_wrapped_exception(isolated_db):
    """Reloading a finished, failed execution from the event log preserves
    the wrapped ExecutionError — the failure value round-trips through the
    dill-backed event store intact.
    """

    @task_decorator
    async def fails() -> str:
        raise ValueError("nope")

    @workflow
    async def wf_replay(ctx: ExecutionContext):
        return await fails()

    first = wf_replay.run()
    assert first.has_failed
    assert isinstance(first.output, ExecutionError)
    assert isinstance(first.output.inner_exception, ValueError)

    # Re-run with the same execution_id. The workflow is already finished,
    # so this reloads the persisted context from the event log; the failure
    # value must survive the pickle/unpickle round-trip unchanged.
    second = wf_replay.run(execution_id=first.execution_id)
    assert second.has_failed
    assert isinstance(second.output, ExecutionError)
    assert isinstance(second.output.inner_exception, ValueError)


def test_completed_workflow_reload_preserves_output(isolated_db):
    """Regression: reloading a finished, succeeded execution preserves the
    stored output (the TASK_FAILED replay branch must not affect success)."""

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
