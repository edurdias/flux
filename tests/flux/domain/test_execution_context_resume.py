"""Tests for the resume-claim state machine additions to ExecutionContext."""

from __future__ import annotations

import pytest

from flux.domain.events import ExecutionEvent, ExecutionEventType, ExecutionState
from flux.domain.execution_context import ExecutionContext
from flux.worker_registry import WorkerInfo


def test_resume_scheduled_state_exists():
    assert ExecutionState.RESUME_SCHEDULED.value == "RESUME_SCHEDULED"


def test_resume_claimed_state_exists():
    assert ExecutionState.RESUME_CLAIMED.value == "RESUME_CLAIMED"


def test_workflow_resume_scheduled_event_type_exists():
    assert ExecutionEventType.WORKFLOW_RESUME_SCHEDULED.value == "WORKFLOW_RESUME_SCHEDULED"


def test_workflow_resume_claimed_event_type_exists():
    assert ExecutionEventType.WORKFLOW_RESUME_CLAIMED.value == "WORKFLOW_RESUME_CLAIMED"


def _make_ctx(state: ExecutionState) -> ExecutionContext:
    ctx: ExecutionContext = ExecutionContext(
        workflow_id="wf-1",
        workflow_name="test_wf",
        workflow_namespace="default",
        input=None,
    )
    ctx._state = state
    return ctx


def _make_worker(name: str = "w-1") -> WorkerInfo:
    return WorkerInfo(name=name, runtime=None, resources=None, packages=[], labels={})


def test_resume_schedule_transitions_state_and_appends_event():
    ctx = _make_ctx(ExecutionState.RESUMING)
    worker = _make_worker("w-1")
    ctx.resume_schedule(worker)
    assert ctx.state == ExecutionState.RESUME_SCHEDULED
    assert ctx.current_worker == "w-1"
    assert ctx.events[-1].type == ExecutionEventType.WORKFLOW_RESUME_SCHEDULED
    assert ctx.events[-1].source_id == "w-1"


def test_resume_schedule_from_wrong_state_raises():
    ctx = _make_ctx(ExecutionState.PAUSED)
    worker = _make_worker("w-1")
    with pytest.raises(Exception):
        ctx.resume_schedule(worker)


def test_resume_claim_transitions_state_and_appends_event():
    ctx = _make_ctx(ExecutionState.RESUME_SCHEDULED)
    ctx._current_worker = "w-1"
    worker = _make_worker("w-1")
    ctx.resume_claim(worker)
    assert ctx.state == ExecutionState.RESUME_CLAIMED
    assert ctx.current_worker == "w-1"
    assert ctx.events[-1].type == ExecutionEventType.WORKFLOW_RESUME_CLAIMED
    assert ctx.events[-1].source_id == "w-1"


def test_resume_claim_from_wrong_state_raises():
    ctx = _make_ctx(ExecutionState.RESUMING)
    worker = _make_worker("w-1")
    with pytest.raises(Exception):
        ctx.resume_claim(worker)


def test_resume_from_resume_claimed_succeeds():
    ctx = _make_ctx(ExecutionState.RESUME_CLAIMED)
    ctx.events.append(
        ExecutionEvent(
            type=ExecutionEventType.WORKFLOW_RESUMING,
            source_id="server",
            name="test_wf",
            value={"message": "hello"},
        ),
    )
    result = ctx.resume()
    assert ctx.state == ExecutionState.RUNNING
    assert ctx.events[-1].type == ExecutionEventType.WORKFLOW_RESUMED
    assert result == {"message": "hello"}


def test_resume_from_non_resume_claimed_raises():
    ctx = _make_ctx(ExecutionState.RESUMING)
    with pytest.raises(Exception):
        ctx.resume()


def test_resume_from_paused_no_longer_auto_starts_resuming():
    ctx = _make_ctx(ExecutionState.PAUSED)
    with pytest.raises(Exception):
        ctx.resume()


def test_is_resuming_true_only_on_resume_claimed():
    for state, expected in [
        (ExecutionState.CREATED, False),
        (ExecutionState.SCHEDULED, False),
        (ExecutionState.CLAIMED, False),
        (ExecutionState.RUNNING, False),
        (ExecutionState.PAUSED, False),
        (ExecutionState.RESUMING, False),
        (ExecutionState.RESUME_SCHEDULED, False),
        (ExecutionState.RESUME_CLAIMED, True),
        (ExecutionState.COMPLETED, False),
        (ExecutionState.FAILED, False),
    ]:
        ctx = _make_ctx(state)
        assert ctx.is_resuming is expected, f"state={state.value}"


def test_resume_uses_latest_resuming_event_for_multi_turn():
    ctx = _make_ctx(ExecutionState.RESUME_CLAIMED)
    ctx.events.append(
        ExecutionEvent(
            type=ExecutionEventType.WORKFLOW_RESUMING,
            source_id="server",
            name="test_wf",
            value={"message": "first"},
        ),
    )
    ctx.events.append(
        ExecutionEvent(
            type=ExecutionEventType.WORKFLOW_RESUMED,
            source_id="w-1",
            name="test_wf",
            value={"message": "first"},
        ),
    )
    # Simulate second turn: user posts a second resume
    ctx.events.append(
        ExecutionEvent(
            type=ExecutionEventType.WORKFLOW_RESUMING,
            source_id="server",
            name="test_wf",
            value={"message": "second"},
        ),
    )
    # Put back into RESUME_CLAIMED as if the server dispatched + worker claimed
    ctx._state = ExecutionState.RESUME_CLAIMED
    result = ctx.resume()
    assert result == {"message": "second"}
