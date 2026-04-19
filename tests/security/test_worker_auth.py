from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from flux.server import Server
from flux.security.identity import FluxIdentity
from flux.worker import WorkflowExecutionRequest


class TestWorkerExecTokenPassthrough:
    def test_execution_request_reads_exec_token_from_payload(self):
        data = {
            "workflow": {"id": "wf-1", "name": "test", "version": 1, "source": "code"},
            "context": {
                "workflow_id": "wf-1",
                "workflow_name": "test",
                "input": None,
                "execution_id": "exec-1",
                "state": "SCHEDULED",
                "events": [],
            },
            "exec_token": "exec.tok.dispatch",
        }
        request = WorkflowExecutionRequest.from_json(data, checkpoint=AsyncMock())
        assert request.exec_token == "exec.tok.dispatch"

    def test_execution_request_no_exec_token(self):
        data = {
            "workflow": {"id": "wf-1", "name": "test", "version": 1, "source": "code"},
            "context": {
                "workflow_id": "wf-1",
                "workflow_name": "test",
                "input": None,
                "execution_id": "exec-1",
                "state": "SCHEDULED",
                "events": [],
            },
        }
        request = WorkflowExecutionRequest.from_json(data, checkpoint=AsyncMock())
        assert request.exec_token is None

    def test_execution_request_has_no_auth_token_field(self):
        data = {
            "workflow": {"id": "wf-1", "name": "test", "version": 1, "source": "code"},
            "context": {
                "workflow_id": "wf-1",
                "workflow_name": "test",
                "input": None,
                "execution_id": "exec-1",
                "state": "SCHEDULED",
                "events": [],
            },
            "auth_token": "should-be-ignored",
        }
        request = WorkflowExecutionRequest.from_json(data, checkpoint=AsyncMock())
        assert not hasattr(
            request,
            "auth_token",
        ), "WorkflowExecutionRequest still has auth_token field"


class TestWorkerSetsExecTokenOnContext:
    def test_from_json_sets_exec_token_on_context(self):
        data = {
            "workflow": {"id": "wf-1", "name": "test", "version": 1, "source": "code"},
            "context": {
                "workflow_id": "wf-1",
                "workflow_name": "test",
                "input": None,
                "execution_id": "exec-42",
                "state": "SCHEDULED",
                "events": [],
            },
            "exec_token": "exec.tok.worker-side",
        }
        request = WorkflowExecutionRequest.from_json(data, checkpoint=AsyncMock())
        assert (
            request.context.exec_token == "exec.tok.worker-side"
        ), "exec_token was not propagated to the ExecutionContext"

    def test_from_json_no_exec_token_context_exec_token_is_none(self):
        data = {
            "workflow": {"id": "wf-1", "name": "test", "version": 1, "source": "code"},
            "context": {
                "workflow_id": "wf-1",
                "workflow_name": "test",
                "input": None,
                "execution_id": "exec-43",
                "state": "SCHEDULED",
                "events": [],
            },
        }
        request = WorkflowExecutionRequest.from_json(data, checkpoint=AsyncMock())
        assert request.context.exec_token is None


@pytest.fixture
def server_app():
    server = Server(host="localhost", port=8000)
    return server._create_api()


@pytest.fixture
def client(server_app):
    return TestClient(server_app)


def _mock_auth(identity: FluxIdentity):
    mock_service = MagicMock()

    async def mock_authenticate(token):
        return identity

    async def mock_is_authorized(ident, permission):
        return True

    mock_service.authenticate = mock_authenticate
    mock_service.is_authorized = mock_is_authorized

    return patch(
        "flux.security.dependencies._get_auth_service",
        return_value=mock_service,
    )


class TestWorkerNameBinding:
    def test_pong_rejects_identity_mismatch(self, client):
        identity = FluxIdentity(
            subject="worker-A",
            roles=frozenset({"worker"}),
        )
        from flux.config import Configuration

        settings = Configuration.get().settings
        original = settings.security.auth.api_keys.enabled
        settings.security.auth.api_keys.enabled = True
        try:
            with _mock_auth(identity):
                resp = client.post(
                    "/workers/worker-B/pong",
                    headers={"Authorization": "Bearer fake-token"},
                )
                assert resp.status_code == 403
                assert "mismatch" in resp.json()["detail"].lower()
        finally:
            settings.security.auth.api_keys.enabled = original

    def test_pong_allows_identity_match(self, client):
        identity = FluxIdentity(
            subject="worker-A",
            roles=frozenset({"worker"}),
        )
        from flux.config import Configuration

        settings = Configuration.get().settings
        original = settings.security.auth.api_keys.enabled
        settings.security.auth.api_keys.enabled = True
        try:
            with _mock_auth(identity):
                resp = client.post(
                    "/workers/worker-A/pong",
                    headers={"Authorization": "Bearer fake-token"},
                )
                assert resp.status_code != 403
        finally:
            settings.security.auth.api_keys.enabled = original

    def test_pong_skips_name_check_when_auth_disabled(self, client):
        from flux.config import Configuration

        settings = Configuration.get().settings
        original_oidc = settings.security.auth.oidc.enabled
        original_keys = settings.security.auth.api_keys.enabled
        settings.security.auth.oidc.enabled = False
        settings.security.auth.api_keys.enabled = False
        try:
            resp = client.post("/workers/any-name/pong")
            assert resp.status_code != 403
        finally:
            settings.security.auth.oidc.enabled = original_oidc
            settings.security.auth.api_keys.enabled = original_keys
