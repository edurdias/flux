"""Regression test for the 683-resumes-per-POST bug.

This test was the smoking gun that prompted the resume-claim design.
Before the fix: a single POST /resume on SQLite produced hundreds of
WORKFLOW_RESUMED events due to a tight re-dispatch loop.
After the fix: exactly one event per atomic transition.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from flux.context_managers import DatabaseContextManager
from flux.domain.events import ExecutionEvent, ExecutionEventType, ExecutionState
from flux.domain.execution_context import ExecutionContext
from flux.models import WorkflowModel
from flux.worker_registry import WorkerInfo


@pytest.fixture
def manager(tmp_path):
    db_path = tmp_path / "test.db"
    db_url = f"sqlite:///{db_path}"
    with patch("flux.config.Configuration.get") as mock_config:
        mock_config.return_value.settings.database_url = db_url
        mock_config.return_value.settings.database_type = "sqlite"
        mock_config.return_value.settings.security.auth.enabled = False
        yield DatabaseContextManager()


def test_single_resume_produces_exactly_one_of_each_event(manager):
    """After one external resume + server dispatch + worker claim, event log has
    exactly one RESUMING / RESUME_SCHEDULED / RESUME_CLAIMED event."""
    with manager.session() as session:
        session.add(
            WorkflowModel(
                id="wf-race",
                namespace="default",
                name="race_wf",
                version=1,
                source=b"",
                imports=[],
            ),
        )
        session.commit()

    ctx = ExecutionContext(
        workflow_id="wf-race",
        workflow_name="race_wf",
        workflow_namespace="default",
        input=None,
        execution_id="exec-race",
    )
    ctx._state = ExecutionState.PAUSED
    ctx.events.append(
        ExecutionEvent(
            type=ExecutionEventType.WORKFLOW_PAUSED,
            source_id="task-1",
            name="race_wf",
            value={"name": "wait_step", "output": None},
        ),
    )
    manager.save(ctx)

    ctx.start_resuming({"message": "hello"})
    manager.save(ctx)

    worker = WorkerInfo(name="w-1")

    first = manager.next_resume(worker)
    second = manager.next_resume(worker)
    assert first is not None, "first next_resume must find the RESUMING row"
    assert second is None, "second next_resume must find nothing (state is RESUME_SCHEDULED)"

    manager.claim_resume("exec-race", worker)

    refreshed = manager.get("exec-race")
    counts = {
        ExecutionEventType.WORKFLOW_RESUMING: 0,
        ExecutionEventType.WORKFLOW_RESUME_SCHEDULED: 0,
        ExecutionEventType.WORKFLOW_RESUME_CLAIMED: 0,
    }
    for e in refreshed.events:
        if e.type in counts:
            counts[e.type] += 1

    assert counts[ExecutionEventType.WORKFLOW_RESUMING] == 1
    assert counts[ExecutionEventType.WORKFLOW_RESUME_SCHEDULED] == 1
    assert counts[ExecutionEventType.WORKFLOW_RESUME_CLAIMED] == 1
    assert refreshed.state == ExecutionState.RESUME_CLAIMED
