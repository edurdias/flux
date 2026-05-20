"""Authorization tests for the approve/reject approval routes.

The approve/reject routes enforce two checks when auth is enabled:
  1. ``workflow:{ns}:{wf}:read`` on the execution's workflow.
  2. ``workflow:{ns}:{wf}:task:{task}:approve`` on the approval row's task.

These tests exercise the denial path with auth enabled — the existing
``tests/flux/test_server_approvals.py`` suite runs with auth disabled, so a
regression in the approval-route permission checks would otherwise go
uncaught.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from flux.approvals import ApprovalManager
from flux.security.identity import FluxIdentity
from flux.unit_of_work import UnitOfWork


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A FluxServer app backed by a fresh on-disk SQLite database."""
    db_path = tmp_path / "approval_authz.db"
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


def _seed_execution(execution_id: str, namespace: str, workflow_name: str) -> None:
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


def _seed_approval(execution_id: str, task_call_id: str) -> None:
    with UnitOfWork() as uow:
        ApprovalManager().create(
            execution_id,
            task_call_id,
            "default",
            "release",
            "deploy",
            uow=uow,
        )
        uow.commit()


def _with_auth(client_, verb: str):
    """POST to the approve/reject route as an identity with no permissions,
    with auth enabled. Returns the response."""
    from flux.config import Configuration

    eid = "exec-authz"
    _seed_execution(eid, "default", "release")
    _seed_approval(eid, "deploy_1")

    unprivileged = FluxIdentity(subject="nobody", roles=frozenset())

    async def _authenticate(_token):
        return unprivileged

    mock_auth = MagicMock()
    mock_auth.authenticate = _authenticate

    settings = Configuration.get().settings
    # auth.enabled is a stored field (not derived from the providers), so the
    # master switch has to be flipped explicitly alongside a provider.
    original_enabled = settings.security.auth.enabled
    original_api_keys = settings.security.auth.api_keys.enabled
    settings.security.auth.enabled = True
    settings.security.auth.api_keys.enabled = True
    try:
        with patch(
            "flux.security.dependencies._get_auth_service",
            return_value=mock_auth,
        ):
            return client_.post(
                f"/executions/{eid}/approvals/deploy_1/{verb}",
                headers={"Authorization": "Bearer fake-token"},
                json={},
            )
    finally:
        settings.security.auth.enabled = original_enabled
        settings.security.auth.api_keys.enabled = original_api_keys


def test_approve_route_denies_unauthorized_identity(client):
    """An identity without the approve/read permissions cannot approve."""
    resp = _with_auth(client, "approve")
    assert resp.status_code in (401, 403), resp.text

    # The approval row must remain pending — the decision was rejected.
    rows = ApprovalManager().list(execution_id="exec-authz", status=None)
    assert rows and all(r.status.value == "pending" for r in rows)


def test_reject_route_denies_unauthorized_identity(client):
    """An identity without the approve/read permissions cannot reject."""
    resp = _with_auth(client, "reject")
    assert resp.status_code in (401, 403), resp.text

    rows = ApprovalManager().list(execution_id="exec-authz", status=None)
    assert rows and all(r.status.value == "pending" for r in rows)
