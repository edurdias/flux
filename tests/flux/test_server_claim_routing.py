"""Tests for workers_claim state-based routing.

Verifies the handler:
- Routes CREATED/SCHEDULED rows to context_manager.claim()
- Routes RESUME_SCHEDULED rows to context_manager.claim_resume()
- Returns 409 for RESUME_CLAIMED rows (already claimed)
- Returns 409 for terminal states (COMPLETED/FAILED/CANCELLED)
- Returns 404 for missing executions
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from flux import ExecutionContext
from flux.domain.events import ExecutionState
from flux.errors import ExecutionContextNotFoundError
from flux.server import Server


@pytest.fixture
def server_instance():
    """Create a server instance for testing."""
    return Server(host="localhost", port=8000)


@pytest.fixture
def server_app(server_instance):
    """Create a server app for testing."""
    return server_instance._create_api()


@pytest.fixture
def test_client(server_app, server_instance):
    """Create a test client with auth bypassed.

    The handler uses require_permission("worker:*:*") + _verify_worker_identity.
    We stub both to keep tests focused on state-based routing.
    """
    from flux.security.identity import FluxIdentity

    worker_identity = FluxIdentity(subject="test-worker", roles=frozenset({"worker"}))
    mock_auth = MagicMock()

    async def mock_authenticate(token):
        return worker_identity

    async def mock_is_authorized(identity, permission):
        return True

    mock_auth.authenticate = mock_authenticate
    mock_auth.is_authorized = mock_is_authorized

    from flux.worker_registry import WorkerInfo

    mock_registry = MagicMock()
    mock_registry.get.return_value = WorkerInfo(name="test-worker")

    with (
        patch.object(server_instance, "_verify_worker_identity"),
        patch("flux.security.dependencies._get_auth_service", return_value=mock_auth),
        patch("flux.server.WorkerRegistry.create", return_value=mock_registry),
    ):
        yield TestClient(server_app)


def _make_ctx(execution_id: str, state: ExecutionState) -> MagicMock:
    """Build a mock ExecutionContext that has the shape used by the handler."""
    ctx = MagicMock(spec=ExecutionContext)
    ctx.execution_id = execution_id
    ctx.state = state
    ctx_dict: dict[str, object] = {
        "execution_id": execution_id,
        "state": state.value,
        "workflow_id": "wf-1",
        "workflow_name": "claim_test_wf",
        "workflow_namespace": "default",
        "input": None,
        "events": [],
    }
    ctx.to_dict = MagicMock(return_value=ctx_dict)
    ctx.summary = MagicMock(
        return_value={k: v for k, v in ctx_dict.items() if k != "events"},
    )
    return ctx


class TestWorkersClaimRouting:
    """Tests for the /workers/{name}/claim/{execution_id} routing logic."""

    @patch("flux.server.ContextManager.create")
    def test_claim_on_created_routes_to_claim(self, mock_cm_create, test_client):
        mock_cm = MagicMock()
        current = _make_ctx("exec-created", ExecutionState.CREATED)
        claimed = _make_ctx("exec-created", ExecutionState.CLAIMED)
        mock_cm.get.return_value = current
        mock_cm.claim.return_value = claimed
        mock_cm_create.return_value = mock_cm

        resp = test_client.post("/workers/test-worker/claim/exec-created")

        assert resp.status_code == 200
        assert resp.json()["state"] == "CLAIMED"
        mock_cm.claim.assert_called_once()
        mock_cm.claim_resume.assert_not_called()

    @patch("flux.server.ContextManager.create")
    def test_claim_on_scheduled_routes_to_claim(self, mock_cm_create, test_client):
        mock_cm = MagicMock()
        current = _make_ctx("exec-sched", ExecutionState.SCHEDULED)
        claimed = _make_ctx("exec-sched", ExecutionState.CLAIMED)
        mock_cm.get.return_value = current
        mock_cm.claim.return_value = claimed
        mock_cm_create.return_value = mock_cm

        resp = test_client.post("/workers/test-worker/claim/exec-sched")

        assert resp.status_code == 200
        assert resp.json()["state"] == "CLAIMED"
        mock_cm.claim.assert_called_once()
        mock_cm.claim_resume.assert_not_called()

    @patch("flux.server.ContextManager.create")
    def test_claim_on_resume_scheduled_routes_to_claim_resume(
        self,
        mock_cm_create,
        test_client,
    ):
        mock_cm = MagicMock()
        current = _make_ctx("exec-rs", ExecutionState.RESUME_SCHEDULED)
        claimed = _make_ctx("exec-rs", ExecutionState.RESUME_CLAIMED)
        mock_cm.get.return_value = current
        mock_cm.claim_resume.return_value = claimed
        mock_cm_create.return_value = mock_cm

        resp = test_client.post("/workers/test-worker/claim/exec-rs")

        assert resp.status_code == 200
        assert resp.json()["state"] == "RESUME_CLAIMED"
        mock_cm.claim_resume.assert_called_once()
        mock_cm.claim.assert_not_called()

    @patch("flux.server.ContextManager.create")
    def test_claim_on_resume_claimed_returns_409(self, mock_cm_create, test_client):
        mock_cm = MagicMock()
        mock_cm.get.return_value = _make_ctx("exec-rc", ExecutionState.RESUME_CLAIMED)
        mock_cm_create.return_value = mock_cm

        resp = test_client.post("/workers/test-worker/claim/exec-rc")

        assert resp.status_code == 409
        mock_cm.claim.assert_not_called()
        mock_cm.claim_resume.assert_not_called()

    @patch("flux.server.ContextManager.create")
    def test_claim_on_completed_returns_409(self, mock_cm_create, test_client):
        mock_cm = MagicMock()
        mock_cm.get.return_value = _make_ctx("exec-done", ExecutionState.COMPLETED)
        mock_cm_create.return_value = mock_cm

        resp = test_client.post("/workers/test-worker/claim/exec-done")

        assert resp.status_code == 409
        mock_cm.claim.assert_not_called()
        mock_cm.claim_resume.assert_not_called()

    @patch("flux.server.ContextManager.create")
    def test_claim_on_failed_returns_409(self, mock_cm_create, test_client):
        mock_cm = MagicMock()
        mock_cm.get.return_value = _make_ctx("exec-failed", ExecutionState.FAILED)
        mock_cm_create.return_value = mock_cm

        resp = test_client.post("/workers/test-worker/claim/exec-failed")

        assert resp.status_code == 409

    @patch("flux.server.ContextManager.create")
    def test_claim_on_cancelled_returns_409(self, mock_cm_create, test_client):
        mock_cm = MagicMock()
        mock_cm.get.return_value = _make_ctx("exec-cancelled", ExecutionState.CANCELLED)
        mock_cm_create.return_value = mock_cm

        resp = test_client.post("/workers/test-worker/claim/exec-cancelled")

        assert resp.status_code == 409

    @patch("flux.server.ContextManager.create")
    def test_claim_on_missing_execution_returns_404(self, mock_cm_create, test_client):
        mock_cm = MagicMock()
        mock_cm.get.side_effect = ExecutionContextNotFoundError("nonexistent")
        mock_cm_create.return_value = mock_cm

        resp = test_client.post("/workers/test-worker/claim/nonexistent")

        assert resp.status_code == 404

    @patch("flux.server.ContextManager.create")
    def test_claim_delegates_to_context_manager(
        self,
        mock_cm_create,
        test_client,
        server_instance,
    ):
        """Successful claim should delegate to context_manager.claim()."""
        mock_cm = MagicMock()
        current = _make_ctx("exec-track", ExecutionState.CREATED)
        claimed = _make_ctx("exec-track", ExecutionState.CLAIMED)
        mock_cm.get.return_value = current
        mock_cm.claim.return_value = claimed
        mock_cm_create.return_value = mock_cm

        resp = test_client.post("/workers/test-worker/claim/exec-track")

        assert resp.status_code == 200
        mock_cm.claim.assert_called_once()

    @patch("flux.server.ContextManager.create")
    def test_claim_resume_delegates_to_context_manager(
        self,
        mock_cm_create,
        test_client,
        server_instance,
    ):
        """Successful claim_resume should delegate to context_manager.claim_resume()."""
        mock_cm = MagicMock()
        current = _make_ctx("exec-rs-track", ExecutionState.RESUME_SCHEDULED)
        claimed = _make_ctx("exec-rs-track", ExecutionState.RESUME_CLAIMED)
        mock_cm.get.return_value = current
        mock_cm.claim_resume.return_value = claimed
        mock_cm_create.return_value = mock_cm

        resp = test_client.post("/workers/test-worker/claim/exec-rs-track")

        assert resp.status_code == 200
        mock_cm.claim_resume.assert_called_once()
