"""Tests for the execution-history retention sweep."""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from flux.domain import ExecutionState
from flux.domain.events import ExecutionEvent, ExecutionEventType
from flux.domain.execution_context import ExecutionContext
from flux.models import (
    ApprovalRequestModel,
    ExecutionContextModel,
    ExecutionEventModel,
    RepositoryFactory,
)
from flux.retention import RetentionJob


@pytest.fixture
def env():
    with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as f:
        db_path = f.name
    db_url = f"sqlite:///{db_path}"
    with patch("flux.config.Configuration.get") as mock_config:
        settings = mock_config.return_value.settings
        settings.database_url = db_url
        settings.database_type = "sqlite"
        settings.security.auth.enabled = False
        settings.retention.enabled = True
        settings.retention.retention_days = 30
        settings.retention.sweep_interval = 3600
        settings.retention.batch_size = 2  # small, to exercise batching
        yield RepositoryFactory.create_repository()
    if os.path.exists(db_path):
        os.unlink(db_path)


def _create_workflow(repo, name="wf"):
    from flux.models import WorkflowModel

    with repo.session() as session:
        wf = WorkflowModel(
            id=f"default/{name}",
            name=name,
            version=1,
            imports=[],
            source=b"async def placeholder(ctx): pass",
            namespace="default",
        )
        session.add(wf)
        session.commit()
        return wf.id


def _create_execution(repo, wf_id, state, age_days, name="wf"):
    ctx = ExecutionContext(workflow_id=wf_id, workflow_namespace="default", workflow_name=name)
    ctx.events.append(
        ExecutionEvent(
            type=ExecutionEventType.WORKFLOW_COMPLETED,
            source_id="test",
            name=name,
            value=None,
        ),
    )
    with repo.session() as session:
        model = ExecutionContextModel.from_plain(ctx)
        model.state = state
        session.add(model)
        session.commit()
        stamp = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=age_days)
        session.query(ExecutionEventModel).filter(
            ExecutionEventModel.execution_id == ctx.execution_id,
        ).update({ExecutionEventModel.time: stamp}, synchronize_session=False)
        session.commit()
    return ctx.execution_id


def _remaining_ids(repo):
    with repo.session() as session:
        return {row[0] for row in session.query(ExecutionContextModel.execution_id).all()}


def test_sweep_deletes_only_old_terminal_executions(env):
    repo = env
    wf_id = _create_workflow(repo)
    old_done = _create_execution(repo, wf_id, ExecutionState.COMPLETED, age_days=45)
    old_failed = _create_execution(repo, wf_id, ExecutionState.FAILED, age_days=45)
    old_running = _create_execution(repo, wf_id, ExecutionState.RUNNING, age_days=45)
    fresh_done = _create_execution(repo, wf_id, ExecutionState.COMPLETED, age_days=5)

    deleted = RetentionJob()._sweep()

    assert deleted == 2
    remaining = _remaining_ids(repo)
    assert old_done not in remaining
    assert old_failed not in remaining
    assert old_running in remaining  # non-terminal is never touched
    assert fresh_done in remaining  # inside the retention window


def test_sweep_removes_dependent_rows(env):
    repo = env
    wf_id = _create_workflow(repo)
    old = _create_execution(repo, wf_id, ExecutionState.COMPLETED, age_days=45)
    with repo.session() as session:
        from flux.models import ApprovalStatus

        session.add(
            ApprovalRequestModel(
                id="appr-1",
                execution_id=old,
                task_call_id="call-1",
                task_name="approve_step",
                workflow_namespace="default",
                workflow_name="wf",
                requested_at=datetime.now(timezone.utc),
                status=ApprovalStatus.PENDING,
            ),
        )
        session.commit()

    assert RetentionJob()._sweep() == 1

    with repo.session() as session:
        assert (
            session.query(ExecutionEventModel)
            .filter(ExecutionEventModel.execution_id == old)
            .count()
            == 0
        )
        assert (
            session.query(ApprovalRequestModel)
            .filter(ApprovalRequestModel.execution_id == old)
            .count()
            == 0
        )


def test_sweep_batches_until_drained(env):
    repo = env
    wf_id = _create_workflow(repo)
    ids = [_create_execution(repo, wf_id, ExecutionState.COMPLETED, age_days=45) for _ in range(5)]

    # batch_size is 2, so a full sweep needs three transactions internally.
    assert RetentionJob()._sweep() == 5
    assert _remaining_ids(repo).isdisjoint(ids)


def test_sweep_with_nothing_expired_is_a_noop(env):
    repo = env
    wf_id = _create_workflow(repo)
    fresh = _create_execution(repo, wf_id, ExecutionState.COMPLETED, age_days=1)

    assert RetentionJob()._sweep() == 0
    assert fresh in _remaining_ids(repo)
