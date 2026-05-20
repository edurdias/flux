"""Concurrent-decide race tests for ApprovalManager.decide."""

from __future__ import annotations

import threading
import uuid

import pytest

from flux.approvals import ApprovalAlreadyDecided, ApprovalManager
from flux.unit_of_work import UnitOfWork


def _seed(execution_id: str, task_call_id: str = "deploy_1") -> None:
    with UnitOfWork() as uow:
        ApprovalManager().create(
            execution_id,
            task_call_id,
            workflow_namespace="default",
            workflow_name="release",
            task_name="deploy",
            uow=uow,
        )
        uow.commit()


def _decide(
    execution_id: str,
    task_call_id: str,
    *,
    approver: str,
    approved: bool,
    barrier: threading.Barrier | None,
    out: list,
) -> None:
    if barrier is not None:
        barrier.wait()
    try:
        with UnitOfWork() as uow:
            ApprovalManager().decide(
                execution_id,
                task_call_id,
                approver_subject=approver,
                approver_provider="oidc",
                approved=approved,
                reason=None,
                uow=uow,
            )
            uow.commit()
        out.append(("ok", approver))
    except ApprovalAlreadyDecided as e:
        out.append(("loser", approver, e.current_status.value))
    except Exception as e:
        out.append(("error", approver, type(e).__name__, str(e)))


def test_concurrent_decide_yields_one_winner_and_one_clean_409(isolated_db):
    eid = f"exec-{uuid.uuid4().hex[:8]}"
    _seed(eid)

    barrier = threading.Barrier(2)
    out: list = []
    t1 = threading.Thread(
        target=_decide,
        kwargs={
            "execution_id": eid,
            "task_call_id": "deploy_1",
            "approver": "alice",
            "approved": True,
            "barrier": barrier,
            "out": out,
        },
    )
    t2 = threading.Thread(
        target=_decide,
        kwargs={
            "execution_id": eid,
            "task_call_id": "deploy_1",
            "approver": "bob",
            "approved": False,
            "barrier": barrier,
            "out": out,
        },
    )
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    statuses = [r[0] for r in out]
    assert statuses.count("ok") == 1, out
    assert statuses.count("loser") == 1, out
    assert "error" not in statuses, out


def test_serial_decide_after_decision_raises_already_decided(isolated_db):
    eid = f"exec-{uuid.uuid4().hex[:8]}"
    _seed(eid)
    mgr = ApprovalManager()
    with UnitOfWork() as uow:
        mgr.decide(
            eid,
            "deploy_1",
            approver_subject="alice",
            approver_provider="oidc",
            approved=True,
            reason=None,
            uow=uow,
        )
        uow.commit()
    with pytest.raises(ApprovalAlreadyDecided) as exc:
        with UnitOfWork() as uow:
            mgr.decide(
                eid,
                "deploy_1",
                approver_subject="bob",
                approver_provider="oidc",
                approved=False,
                reason=None,
                uow=uow,
            )
            uow.commit()
    assert exc.value.current_status.value == "approved"
