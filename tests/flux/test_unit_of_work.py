"""UnitOfWork transaction-semantics tests.

Commit / rollback behaviour is asserted with a real ORM row (an
``ApprovalRequestModel``) read back from a *separate* session, so the tests
verify actual persistence rather than merely that ``commit()`` does not raise.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from flux.approvals import ApprovalManager
from flux.models import ApprovalRequestModel, ApprovalStatus
from flux.unit_of_work import UnitOfWork


def _make_approval(approval_id: str) -> ApprovalRequestModel:
    return ApprovalRequestModel(
        id=approval_id,
        execution_id=f"uow-{approval_id}",
        task_call_id="task_call",
        workflow_namespace="default",
        workflow_name="wf",
        task_name="task",
        requested_at=datetime.now(timezone.utc),
        status=ApprovalStatus.PENDING,
    )


def test_uow_provides_session():
    with UnitOfWork() as uow:
        result = uow.session.execute(text("SELECT 1")).scalar()
        assert result == 1


def test_uow_commit_persists_changes(isolated_db):
    """A row added and committed inside a UoW is visible from a later session."""
    approval_id = uuid.uuid4().hex
    with UnitOfWork() as uow:
        uow.session.add(_make_approval(approval_id))
        uow.commit()

    fetched = ApprovalManager().get(approval_id)
    assert fetched is not None
    assert fetched.id == approval_id
    assert fetched.status == ApprovalStatus.PENDING


def test_uow_rollback_discards_changes(isolated_db):
    """A row added then explicitly rolled back is never persisted."""
    approval_id = uuid.uuid4().hex
    with UnitOfWork() as uow:
        uow.session.add(_make_approval(approval_id))
        uow.rollback()

    assert ApprovalManager().get(approval_id) is None


def test_uow_exit_without_commit_rolls_back(isolated_db):
    """Leaving the with-block without commit() discards the writes."""
    approval_id = uuid.uuid4().hex
    with UnitOfWork() as uow:
        uow.session.add(_make_approval(approval_id))
        # No explicit commit — implicit rollback on exit.

    assert ApprovalManager().get(approval_id) is None


def test_uow_exception_rolls_back_and_propagates(isolated_db):
    class Boom(Exception):
        pass

    approval_id = uuid.uuid4().hex
    with pytest.raises(Boom):
        with UnitOfWork() as uow:
            uow.session.add(_make_approval(approval_id))
            raise Boom("fail")

    assert ApprovalManager().get(approval_id) is None
