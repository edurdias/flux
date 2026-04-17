"""Tests for the resume-claim state machine additions to ExecutionContext."""

from __future__ import annotations

from flux.domain.events import ExecutionEventType, ExecutionState


def test_resume_scheduled_state_exists():
    assert ExecutionState.RESUME_SCHEDULED.value == "RESUME_SCHEDULED"


def test_resume_claimed_state_exists():
    assert ExecutionState.RESUME_CLAIMED.value == "RESUME_CLAIMED"


def test_workflow_resume_scheduled_event_type_exists():
    assert ExecutionEventType.WORKFLOW_RESUME_SCHEDULED.value == "WORKFLOW_RESUME_SCHEDULED"


def test_workflow_resume_claimed_event_type_exists():
    assert ExecutionEventType.WORKFLOW_RESUME_CLAIMED.value == "WORKFLOW_RESUME_CLAIMED"
