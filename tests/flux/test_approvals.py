import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from flux.approvals import (
    ApprovalAlreadyDecided,
    ApprovalManager,
    ApprovalRejected,
    ApprovalVerdict,
)
from flux.models import ApprovalRequestModel, ApprovalStatus, RepositoryFactory
from flux.unit_of_work import UnitOfWork


def test_approval_status_enum_values():
    assert ApprovalStatus.PENDING == "pending"
    assert ApprovalStatus.APPROVED == "approved"
    assert ApprovalStatus.REJECTED == "rejected"
    assert ApprovalStatus.CANCELLED == "cancelled"


def test_approval_request_model_persists_and_loads(isolated_db):
    repo = RepositoryFactory.create_repository()
    with repo.session() as s:
        row = ApprovalRequestModel(
            id="ap-test-1",
            execution_id="exec-1",
            task_call_id="call-1",
            workflow_namespace="default",
            workflow_name="release",
            task_name="deploy_to_prod",
            requested_at=datetime.now(timezone.utc),
            status=ApprovalStatus.PENDING,
        )
        s.add(row)
        s.commit()

        loaded = s.execute(
            select(ApprovalRequestModel).where(ApprovalRequestModel.id == "ap-test-1"),
        ).scalar_one()
        assert loaded.status == ApprovalStatus.PENDING
        assert loaded.workflow_namespace == "default"
        assert loaded.task_name == "deploy_to_prod"


def test_approval_request_unique_per_execution_and_call(isolated_db):
    repo = RepositoryFactory.create_repository()
    now = datetime.now(timezone.utc)
    with repo.session() as s:
        s.add(
            ApprovalRequestModel(
                id="ap-uniq-1",
                execution_id="exec-2",
                task_call_id="call-2",
                workflow_namespace="x",
                workflow_name="y",
                task_name="z",
                requested_at=now,
                status=ApprovalStatus.PENDING,
            ),
        )
        s.commit()
    with pytest.raises(Exception):
        with repo.session() as s:
            s.add(
                ApprovalRequestModel(
                    id="ap-uniq-2",
                    execution_id="exec-2",
                    task_call_id="call-2",
                    workflow_namespace="x",
                    workflow_name="y",
                    task_name="z",
                    requested_at=now,
                    status=ApprovalStatus.PENDING,
                ),
            )
            s.commit()


def _new_call_id() -> str:
    return f"call-{uuid.uuid4().hex[:8]}"


def test_approval_rejected_carries_context():
    err = ApprovalRejected(
        task_name="default/release/deploy_to_prod",
        approver_subject="alice@example.com",
        approver_provider="oidc",
        reason="failed canary",
    )
    assert err.task_name == "default/release/deploy_to_prod"
    assert err.approver_subject == "alice@example.com"
    assert err.reason == "failed canary"
    assert "deploy_to_prod" in str(err)
    assert "alice@example.com" in str(err)


def test_approval_rejected_round_trips_through_pickle():
    """ApprovalRejected is persisted in the event log (dill) when a rejected
    approval emits a TASK_FAILED event. A keyword-only constructor would
    break unpickling, so the structured fields must survive a round-trip.
    """
    import pickle

    import dill

    err = ApprovalRejected(
        task_name="default/release/deploy_to_prod",
        approver_subject="alice@example.com",
        approver_provider="oidc",
        reason="failed canary",
    )
    for loads, dumps in ((pickle.loads, pickle.dumps), (dill.loads, dill.dumps)):
        restored = loads(dumps(err))
        assert isinstance(restored, ApprovalRejected)
        assert restored.task_name == "default/release/deploy_to_prod"
        assert restored.approver_subject == "alice@example.com"
        assert restored.approver_provider == "oidc"
        assert restored.reason == "failed canary"
        assert str(restored) == str(err)


def test_approval_verdict_approved():
    v = ApprovalVerdict(
        approved=True,
        approver_subject="alice",
        approver_provider="oidc",
        reason=None,
    )
    assert v.approved is True
    assert v.cancelled is False


def test_approval_verdict_cancelled():
    v = ApprovalVerdict(approved=False, cancelled=True)
    assert v.approved is False
    assert v.cancelled is True


def test_create_inserts_pending_row(isolated_db):
    mgr = ApprovalManager()
    cid = _new_call_id()
    with UnitOfWork() as uow:
        mgr.create(
            execution_id="exec-c1",
            task_call_id=cid,
            workflow_namespace="default",
            workflow_name="release",
            task_name="deploy_to_prod",
            uow=uow,
        )
        uow.commit()
    fetched = mgr.get_by_call("exec-c1", cid)
    assert fetched is not None
    assert fetched.status == ApprovalStatus.PENDING
    assert fetched.workflow_namespace == "default"


def test_decide_approve_marks_row_and_records_approver(isolated_db):
    mgr = ApprovalManager()
    cid = _new_call_id()
    with UnitOfWork() as uow:
        mgr.create(
            execution_id="exec-d1",
            task_call_id=cid,
            workflow_namespace="x",
            workflow_name="y",
            task_name="z",
            uow=uow,
        )
        uow.commit()
    with UnitOfWork() as uow:
        result = mgr.decide(
            execution_id="exec-d1",
            task_call_id=cid,
            approver_subject="alice",
            approver_provider="oidc",
            approved=True,
            reason="lgtm",
            uow=uow,
        )
        uow.commit()
    assert result.status == ApprovalStatus.APPROVED
    assert result.approver_subject == "alice"
    assert result.reason == "lgtm"


def test_decide_on_already_decided_row_raises_conflict(isolated_db):
    mgr = ApprovalManager()
    cid = _new_call_id()
    with UnitOfWork() as uow:
        mgr.create(
            execution_id="exec-r1",
            task_call_id=cid,
            workflow_namespace="x",
            workflow_name="y",
            task_name="z",
            uow=uow,
        )
        uow.commit()
    with UnitOfWork() as uow:
        mgr.decide(
            "exec-r1",
            cid,
            approver_subject="a",
            approver_provider="oidc",
            approved=True,
            reason=None,
            uow=uow,
        )
        uow.commit()
    with pytest.raises(ApprovalAlreadyDecided) as exc_info:
        with UnitOfWork() as uow:
            mgr.decide(
                "exec-r1",
                cid,
                approver_subject="b",
                approver_provider="oidc",
                approved=False,
                reason=None,
                uow=uow,
            )
            uow.commit()
    assert exc_info.value.current_status == ApprovalStatus.APPROVED


def test_list_filters_by_status_and_execution(isolated_db):
    mgr = ApprovalManager()
    eid = "exec-l1"
    cid_a = _new_call_id()
    cid_b = _new_call_id()
    with UnitOfWork() as uow:
        mgr.create(eid, cid_a, "ns1", "wf1", "t1", uow=uow)
        mgr.create(eid, cid_b, "ns1", "wf1", "t2", uow=uow)
        uow.commit()
    pending = mgr.list(status=ApprovalStatus.PENDING, execution_id=eid)
    call_ids = {r.task_call_id for r in pending}
    assert cid_a in call_ids and cid_b in call_ids


def test_list_paginates(isolated_db):
    mgr = ApprovalManager()
    with UnitOfWork() as uow:
        for i in range(5):
            mgr.create(f"exec-pag-{i}", f"call-pag-{i}", "ns", "wf", f"t{i}", uow=uow)
        uow.commit()
    page1 = mgr.list(limit=2, offset=0)
    page2 = mgr.list(limit=2, offset=2)
    assert len(page1) <= 2 and len(page2) <= 2
    assert {r.id for r in page1}.isdisjoint({r.id for r in page2})


def test_cancel_pending_for_execution(isolated_db):
    mgr = ApprovalManager()
    eid = "exec-cnl-1"
    with UnitOfWork() as uow:
        mgr.create(eid, "call-c1", "ns", "wf", "t1", uow=uow)
        mgr.create(eid, "call-c2", "ns", "wf", "t2", uow=uow)
        uow.commit()
    with UnitOfWork() as uow:
        count = mgr.cancel_pending_for_execution(eid, uow=uow)
        uow.commit()
    assert count == 2
    rows = mgr.list(execution_id=eid)
    statuses = {r.task_call_id: r.status for r in rows}
    assert statuses["call-c1"] == ApprovalStatus.CANCELLED
    assert statuses["call-c2"] == ApprovalStatus.CANCELLED
