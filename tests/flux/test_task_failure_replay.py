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


def test_failed_fallback_records_terminal_event_and_replay_skips(isolated_db):
    """When the fallback itself fails, a terminal TASK_FAILED event must be
    recorded — without it, replay after a pause finds no terminal event and
    re-executes the body AND the fallback (duplicated side effects, and a
    possibly different branch than the original run)."""
    from flux.tasks import pause

    body_runs = [0]
    fallback_runs = [0]

    async def bad_fallback() -> str:
        fallback_runs[0] += 1
        raise RuntimeError("fallback also broke")

    @task_decorator.with_options(fallback=bad_fallback)
    async def flaky() -> str:
        body_runs[0] += 1
        raise ValueError("body broke")

    @workflow
    async def wf_fb(ctx: ExecutionContext):
        try:
            await flaky()
        except ExecutionError:
            pass
        await pause("gate")
        return "done"

    ctx = wf_fb.run()
    assert ctx.is_paused
    assert body_runs[0] == 1 and fallback_runs[0] == 1
    failed = [e for e in ctx.events if e.type == ExecutionEventType.TASK_FAILED]
    assert len(failed) == 1, "fallback failure must leave a terminal TASK_FAILED"

    resumed = wf_fb.run(execution_id=ctx.execution_id)
    assert resumed.has_succeeded
    assert resumed.output == "done"
    # Replay re-raised the stored failure instead of re-running body/fallback.
    assert body_runs[0] == 1 and fallback_runs[0] == 1


def test_failed_rollback_records_terminal_event(isolated_db):
    """A failing rollback must also leave a terminal TASK_FAILED behind."""
    rollback_runs = [0]

    async def bad_rollback() -> None:
        rollback_runs[0] += 1
        raise RuntimeError("rollback broke")

    @task_decorator.with_options(rollback=bad_rollback)
    async def flaky_rb() -> str:
        raise ValueError("body broke")

    @workflow
    async def wf_rb(ctx: ExecutionContext):
        return await flaky_rb()

    ctx = wf_rb.run()
    assert ctx.has_failed
    assert rollback_runs[0] == 1
    failed = [e for e in ctx.events if e.type == ExecutionEventType.TASK_FAILED]
    assert len(failed) == 1, "rollback failure must leave a terminal TASK_FAILED"


def test_wire_degraded_failure_replays_as_exception_not_typeerror(isolated_db):
    """A TASK_FAILED value that crossed a JSON checkpoint hop degrades to
    {"type", "message"} (FluxEncoder); replaying it used to execute
    `raise <dict>` -> TypeError. It must re-raise a real exception with the
    recorded detail."""
    import pytest

    @task_decorator
    async def fails_wire() -> str:
        raise ValueError("nope")

    @workflow
    async def wf_wire(ctx: ExecutionContext):
        return await fails_wire()

    first = wf_wire.run()
    assert first.has_failed

    # Simulate the distributed hop: to_dict (FluxEncoder JSON) -> from_json,
    # exactly what worker claims and runner children do. The stored exception
    # degrades to {"type", "message"} on that hop.
    rebuilt = ExecutionContext.from_json(first.to_dict())

    async def _replay():
        token = ExecutionContext.set(rebuilt)
        try:
            # Direct call with the same args -> same occurrence-0 task id,
            # so the replay short-circuit consumes the degraded TASK_FAILED.
            await fails_wire()
        finally:
            ExecutionContext.reset(token)

    import asyncio

    with pytest.raises(ExecutionError) as excinfo:
        asyncio.run(_replay())
    assert "nope" in str(excinfo.value)


def test_revive_stored_failure_reconstructs_types():
    """Unit contract for the degraded-value reviver."""
    from flux.approvals import ApprovalRejected
    from flux.task import task as task_cls

    revived = task_cls._revive_stored_failure(
        {"type": "ExecutionError", "message": "boom"},
        "t",
    )
    assert isinstance(revived, ExecutionError)
    assert "boom" in str(revived)

    rejected = task_cls._revive_stored_failure(
        {"type": "ApprovalRejected", "message": "denied by alice"},
        "deploy",
    )
    assert isinstance(rejected, ApprovalRejected)

    passthrough = ValueError("as-is")
    assert task_cls._revive_stored_failure(passthrough, "t") is passthrough
