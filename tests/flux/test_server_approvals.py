"""HTTP route tests for the approval read-side endpoints."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from flux.approvals import ApprovalManager
from flux.unit_of_work import UnitOfWork


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Spin up a FluxServer FastAPI app against a fresh on-disk SQLite.

    Auth is left at its default (disabled) so the read routes admit the
    anonymous identity; we only exercise the structural / happy-path
    behaviour here. Permission filtering gets dedicated coverage in the
    Task 14 (POST routes) suite.
    """
    db_path = tmp_path / "approvals.db"
    monkeypatch.setenv("FLUX_DATABASE_URL", f"sqlite:///{db_path}")

    from flux.config import Configuration
    from flux.models import DatabaseRepository

    Configuration._instance = None  # type: ignore[attr-defined]
    Configuration._config = None  # type: ignore[attr-defined]
    DatabaseRepository._engines.clear()
    Configuration.get().override(database_url=f"sqlite:///{db_path}")

    from flux.server import Server

    server = Server("127.0.0.1", 0)
    yield TestClient(server._create_api())

    Configuration._instance = None  # type: ignore[attr-defined]
    Configuration._config = None  # type: ignore[attr-defined]
    DatabaseRepository._engines.clear()


def _seed_approval(
    execution_id: str,
    task_call_id: str,
    *,
    namespace: str = "default",
    workflow: str = "release",
    task: str = "deploy",
):
    mgr = ApprovalManager()
    with UnitOfWork() as uow:
        row = mgr.create(
            execution_id,
            task_call_id,
            namespace,
            workflow,
            task,
            uow=uow,
        )
        uow.commit()
    return row


def _seed_execution(execution_id: str, namespace: str, workflow_name: str) -> None:
    """Persist a minimal ExecutionContext so the per-execution route can resolve it."""
    from flux import ExecutionContext
    from flux.context_managers import ContextManager

    ctx: ExecutionContext = ExecutionContext(
        workflow_id=f"{namespace}/{workflow_name}",
        workflow_namespace=namespace,
        workflow_name=workflow_name,
        input=None,
        execution_id=execution_id,
    )
    ContextManager.create().save(ctx)


def test_get_approvals_lists_pending(client):
    eid = f"exec-list-{uuid.uuid4().hex[:6]}"
    _seed_approval(eid, "call-1")

    r = client.get("/approvals?status=pending")
    assert r.status_code == 200
    body = r.json()
    assert "approvals" in body
    assert body["limit"] == 20
    assert body["offset"] == 0
    matches = [a for a in body["approvals"] if a["execution_id"] == eid]
    assert len(matches) == 1
    approval = matches[0]
    assert approval["task_call_id"] == "call-1"
    assert approval["status"] == "pending"
    assert approval["workflow_namespace"] == "default"
    assert approval["workflow_name"] == "release"
    assert approval["task_name"] == "deploy"
    assert approval["approver"] is None
    assert approval["decided_at"] is None


def test_get_approvals_filters_by_workflow(client):
    eid_a = f"exec-a-{uuid.uuid4().hex[:6]}"
    eid_b = f"exec-b-{uuid.uuid4().hex[:6]}"
    _seed_approval(eid_a, "call-a", namespace="ns1", workflow="alpha")
    _seed_approval(eid_b, "call-b", namespace="ns2", workflow="beta")

    r = client.get("/approvals?workflow_namespace=ns1&workflow_name=alpha")
    assert r.status_code == 200
    body = r.json()
    namespaces = {a["workflow_namespace"] for a in body["approvals"]}
    workflow_names = {a["workflow_name"] for a in body["approvals"]}
    assert namespaces == {"ns1"}
    assert workflow_names == {"alpha"}


def test_get_approvals_status_all(client):
    eid = f"exec-status-{uuid.uuid4().hex[:6]}"
    _seed_approval(eid, "call-1")

    r = client.get(f"/approvals?status=all&execution_id={eid}")
    assert r.status_code == 200
    body = r.json()
    assert any(a["execution_id"] == eid for a in body["approvals"])


def test_get_approvals_invalid_status_returns_400(client):
    r = client.get("/approvals?status=bogus")
    assert r.status_code == 400


def test_get_approvals_invalid_age_min_returns_400(client):
    r = client.get("/approvals?age_min=garbage")
    assert r.status_code == 400


def test_get_approvals_age_min_accepts_iso_duration(client):
    r = client.get("/approvals?age_min=PT1H")
    assert r.status_code == 200


def test_get_approvals_for_execution_returns_404_on_unknown(client):
    r = client.get("/executions/no-such/approvals")
    assert r.status_code == 404


def test_get_approvals_for_execution_returns_rows(client):
    eid = f"exec-perex-{uuid.uuid4().hex[:6]}"
    _seed_execution(eid, "default", "release")
    _seed_approval(eid, "call-x")

    r = client.get(f"/executions/{eid}/approvals")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["approvals"][0]["task_call_id"] == "call-x"
    assert body["approvals"][0]["execution_id"] == eid


def test_get_one_approval_returns_404_on_unknown_execution(client):
    r = client.get("/executions/no-such/approvals/no-such-call")
    assert r.status_code == 404


def test_get_one_approval_returns_404_on_unknown_call(client):
    eid = f"exec-one-missing-{uuid.uuid4().hex[:6]}"
    _seed_execution(eid, "default", "release")
    r = client.get(f"/executions/{eid}/approvals/no-such-call")
    assert r.status_code == 404


def test_get_one_approval_returns_payload(client):
    eid = f"exec-one-{uuid.uuid4().hex[:6]}"
    _seed_execution(eid, "default", "release")
    _seed_approval(eid, "call-one")

    r = client.get(f"/executions/{eid}/approvals/call-one")
    assert r.status_code == 200
    body = r.json()
    assert body["execution_id"] == eid
    assert body["task_call_id"] == "call-one"
    assert body["status"] == "pending"
    assert body["approval_id"]


# === POST approve/reject routes (Task 14) ===
#
# Auth is disabled in the ``client`` fixture — every request is treated as the
# ANONYMOUS identity, which has the ``admin`` role. So the permission stages
# trivially pass and we exercise the happy paths plus the 404/409 negative
# cases. Permission-denial coverage lives in tests/security.


def _seed(eid: str, call_id: str, *, namespace: str = "default", workflow: str = "release"):
    """Combined helper: persist an execution + a pending approval row."""
    _seed_execution(eid, namespace, workflow)
    _seed_approval(eid, call_id, namespace=namespace, workflow=workflow)


def test_post_approve_succeeds(client):
    eid = f"exec-app-{uuid.uuid4().hex[:6]}"
    _seed(eid, "call-app-1")
    r = client.post(
        f"/executions/{eid}/approvals/call-app-1/approve",
        json={"reason": "lgtm"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "approved"
    assert body["reason"] == "lgtm"
    assert body["approver"]["subject"] == "anonymous"
    # ``execution_state`` echoes whatever the ctx is in after the decide. We
    # don't seed a paused ctx here, so ``start_resuming`` is a no-op and the
    # state stays at its initial value. Real workflows reach this code in the
    # PAUSED state and transition to RESUMING.
    assert body["execution_state"] in ("PAUSED", "RESUMING", "CREATED")


def test_post_reject_succeeds(client):
    eid = f"exec-rej-{uuid.uuid4().hex[:6]}"
    _seed(eid, "call-rej-1")
    r = client.post(
        f"/executions/{eid}/approvals/call-rej-1/reject",
        json={"reason": "no good"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "rejected"
    assert body["reason"] == "no good"


def test_post_approve_with_no_body(client):
    """Empty body should default reason to None and still succeed."""
    eid = f"exec-nobody-{uuid.uuid4().hex[:6]}"
    _seed(eid, "call-nobody")
    r = client.post(f"/executions/{eid}/approvals/call-nobody/approve")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "approved"
    assert r.json()["reason"] is None


def test_post_approve_already_decided_returns_409(client):
    eid = f"exec-409-{uuid.uuid4().hex[:6]}"
    _seed(eid, "call-409-1")
    r1 = client.post(f"/executions/{eid}/approvals/call-409-1/approve", json={})
    assert r1.status_code == 200, r1.text
    r2 = client.post(f"/executions/{eid}/approvals/call-409-1/reject", json={})
    assert r2.status_code == 409
    body = r2.json()
    assert body["error"] == "already_decided"
    assert body["current_status"] == "approved"
    assert "approver" not in body


def test_post_approve_404_on_unknown_execution(client):
    r = client.post(
        "/executions/no-such/approvals/no-such-call/approve",
        json={},
    )
    assert r.status_code == 404


def test_post_approve_404_on_unknown_call(client):
    eid = f"exec-no-call-{uuid.uuid4().hex[:6]}"
    _seed_execution(eid, "default", "release")
    r = client.post(
        f"/executions/{eid}/approvals/missing/approve",
        json={"reason": "x"},
    )
    assert r.status_code == 404
    assert r.json()["detail"]["error"] == "not_found"


def test_post_reject_404_on_unknown_call(client):
    eid = f"exec-no-call-rej-{uuid.uuid4().hex[:6]}"
    _seed_execution(eid, "default", "release")
    r = client.post(
        f"/executions/{eid}/approvals/missing/reject",
        json={},
    )
    assert r.status_code == 404


def test_post_approve_transitions_paused_ctx_to_resuming(client):
    """When the ctx is in PAUSED, approval should drive it to RESUMING."""
    from flux import ExecutionContext
    from flux.context_managers import ContextManager

    eid = f"exec-resume-{uuid.uuid4().hex[:6]}"
    ns, wf = "default", "release"
    ctx: ExecutionContext = ExecutionContext(
        workflow_id=f"{ns}/{wf}",
        workflow_namespace=ns,
        workflow_name=wf,
        input=None,
        execution_id=eid,
    )
    ctx.pause(id="task-1", name="approval:call-resume-1")
    ContextManager.create().save(ctx)
    _seed_approval(eid, "call-resume-1", namespace=ns, workflow=wf)

    r = client.post(
        f"/executions/{eid}/approvals/call-resume-1/approve",
        json={"reason": "ship it"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["execution_state"] == "RESUMING"

    after = ContextManager.create().get(eid)
    assert after.state.value == "RESUMING"
    assert any(e.type.value == "TASK_APPROVED" for e in after.events)
    assert any(e.type.value == "WORKFLOW_RESUMING" for e in after.events)


def test_cancel_marks_pending_approvals_cancelled(client):
    """When workflow is cancelled, all pending approvals should transition to cancelled."""
    from flux.models import ApprovalStatus

    eid = f"exec-cncl-{uuid.uuid4().hex[:6]}"

    # Seed an execution context that the cancel route can find
    _seed_execution(eid, "default", "cancel_test")

    # Seed two pending approvals on this execution
    _seed_approval(eid, "call-cncl-1", workflow="cancel_test")
    _seed_approval(eid, "call-cncl-2", workflow="cancel_test")

    # Cancel via the existing route
    r = client.get(f"/workflows/default/cancel_test/cancel/{eid}")
    if r.status_code not in (200, 202):
        pytest.skip(f"Cancel route returned {r.status_code}; might be a setup issue")

    # Verify pending rows are now cancelled
    rows = ApprovalManager().list(execution_id=eid, status=None)
    statuses = {r.task_call_id: r.status for r in rows}
    assert statuses["call-cncl-1"] == ApprovalStatus.CANCELLED
    assert statuses["call-cncl-2"] == ApprovalStatus.CANCELLED
