"""State-precedence regression tests for save/update vs decide/cancel races."""

from __future__ import annotations

from typing import cast

from flux.context_managers import ContextManager, DatabaseContextManager
from flux.domain import ExecutionContext, ExecutionState
from flux.domain.events import ExecutionEvent, ExecutionEventType


def _make_ctx(state: ExecutionState = ExecutionState.RUNNING) -> ExecutionContext:
    ctx: ExecutionContext = ExecutionContext(
        workflow_id="wf-precedence",
        workflow_namespace="default",
        workflow_name="precedence",
        input=None,
    )
    ctx._state = state
    return ctx


def _set_db_state(execution_id: str, state: ExecutionState) -> None:
    cm = cast(DatabaseContextManager, ContextManager.create())
    with cm.session() as session:
        from flux.models import ExecutionContextModel

        row = session.get(ExecutionContextModel, execution_id)
        assert row is not None
        row.state = state
        session.commit()


# --- start_resuming -------------------------------------------------------


def test_start_resuming_transitions_even_when_not_paused():
    ctx = _make_ctx(ExecutionState.RUNNING)
    ctx.start_resuming()
    assert ctx.state == ExecutionState.RESUMING
    assert any(e.type == ExecutionEventType.WORKFLOW_RESUMING for e in ctx.events)


def test_start_resuming_from_paused_still_works():
    ctx = _make_ctx(ExecutionState.RUNNING)
    ctx.events.append(
        ExecutionEvent(
            type=ExecutionEventType.WORKFLOW_PAUSED,
            source_id="wf",
            name="precedence",
            value=None,
        ),
    )
    ctx._state = ExecutionState.PAUSED
    ctx.start_resuming()
    assert ctx.state == ExecutionState.RESUMING


# --- save() state precedence ---------------------------------------------


def test_save_does_not_demote_resuming_to_paused(isolated_db):
    ctx = _make_ctx(ExecutionState.RUNNING)
    cm = ContextManager.create()
    cm.save(ctx)
    _set_db_state(ctx.execution_id, ExecutionState.RESUMING)

    ctx.pause("wf", "approval", output=None)
    assert ctx.state == ExecutionState.PAUSED
    cm.save(ctx)

    fetched = cm.get(ctx.execution_id)
    assert fetched.state == ExecutionState.RESUMING
    paused_events = [e for e in fetched.events if e.type == ExecutionEventType.WORKFLOW_PAUSED]
    assert len(paused_events) == 1


def test_save_does_not_demote_cancelling_to_paused(isolated_db):
    ctx = _make_ctx(ExecutionState.RUNNING)
    cm = ContextManager.create()
    cm.save(ctx)
    _set_db_state(ctx.execution_id, ExecutionState.CANCELLING)

    ctx.pause("wf", "stale", output=None)
    cm.save(ctx)

    fetched = cm.get(ctx.execution_id)
    assert fetched.state == ExecutionState.CANCELLING


def test_save_allows_paused_from_resume_claimed(isolated_db):
    ctx = _make_ctx(ExecutionState.RUNNING)
    cm = ContextManager.create()
    cm.save(ctx)
    _set_db_state(ctx.execution_id, ExecutionState.RESUME_CLAIMED)

    ctx.pause("wf", "next-pause-point", output=None)
    cm.save(ctx)

    fetched = cm.get(ctx.execution_id)
    assert fetched.state == ExecutionState.PAUSED


def test_save_terminal_always_wins_over_resuming(isolated_db):
    ctx = _make_ctx(ExecutionState.RUNNING)
    cm = ContextManager.create()
    cm.save(ctx)
    _set_db_state(ctx.execution_id, ExecutionState.RESUMING)

    ctx.complete("wf", output={"value": 1})
    cm.save(ctx)

    fetched = cm.get(ctx.execution_id)
    assert fetched.state == ExecutionState.COMPLETED


def test_save_normal_pause_still_works(isolated_db):
    ctx = _make_ctx(ExecutionState.RUNNING)
    cm = ContextManager.create()
    cm.save(ctx)

    ctx.pause("wf", "name", output=None)
    cm.save(ctx)

    fetched = cm.get(ctx.execution_id)
    assert fetched.state == ExecutionState.PAUSED


def test_save_does_not_resurrect_terminal_state(isolated_db):
    """A stale RUNNING checkpoint must not overwrite a finished workflow."""
    ctx = _make_ctx(ExecutionState.RUNNING)
    cm = ContextManager.create()
    cm.save(ctx)
    _set_db_state(ctx.execution_id, ExecutionState.COMPLETED)

    ctx._state = ExecutionState.RUNNING
    cm.save(ctx)

    fetched = cm.get(ctx.execution_id)
    assert fetched.state == ExecutionState.COMPLETED


def test_save_does_not_demote_cancelling_to_running(isolated_db):
    """A non-terminal write must not demote a persisted CANCELLING state."""
    ctx = _make_ctx(ExecutionState.RUNNING)
    cm = ContextManager.create()
    cm.save(ctx)
    _set_db_state(ctx.execution_id, ExecutionState.CANCELLING)

    ctx._state = ExecutionState.RUNNING
    cm.save(ctx)

    fetched = cm.get(ctx.execution_id)
    assert fetched.state == ExecutionState.CANCELLING


def test_save_allows_cancelling_to_reach_terminal(isolated_db):
    """CANCELLING may still advance to a terminal CANCELLED state."""
    ctx = _make_ctx(ExecutionState.RUNNING)
    cm = ContextManager.create()
    cm.save(ctx)
    _set_db_state(ctx.execution_id, ExecutionState.CANCELLING)

    ctx._state = ExecutionState.CANCELLED
    cm.save(ctx)

    fetched = cm.get(ctx.execution_id)
    assert fetched.state == ExecutionState.CANCELLED


# --- update() state precedence -------------------------------------------


def test_update_does_not_demote_resuming_to_paused(isolated_db):
    ctx = _make_ctx(ExecutionState.RUNNING)
    cm = ContextManager.create()
    cm.save(ctx)
    _set_db_state(ctx.execution_id, ExecutionState.RESUMING)

    ctx.pause("wf", "stale", output=None)
    cm.update(ctx)

    fetched = cm.get(ctx.execution_id)
    assert fetched.state == ExecutionState.RESUMING


def test_update_does_not_demote_cancelling_to_paused(isolated_db):
    ctx = _make_ctx(ExecutionState.RUNNING)
    cm = ContextManager.create()
    cm.save(ctx)
    _set_db_state(ctx.execution_id, ExecutionState.CANCELLING)

    ctx.pause("wf", "stale", output=None)
    cm.update(ctx)

    fetched = cm.get(ctx.execution_id)
    assert fetched.state == ExecutionState.CANCELLING
