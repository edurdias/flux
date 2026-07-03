"""Tests for operational endpoints and lifecycle GC added for production readiness."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from flux.server import Server


@pytest.fixture
def client():
    server = Server(host="localhost", port=8000)
    return TestClient(server._create_api(), raise_server_exceptions=False)


class TestReadinessProbe:
    def test_ready_returns_200_when_database_reachable(self, client):
        response = client.get("/ready")
        assert response.status_code == 200
        assert response.json() == {"status": "ready", "database": True}

    def test_ready_returns_503_when_database_down(self, client):
        with patch("flux.api.system_routes.WorkflowCatalog.create") as create:
            create.return_value.health_check.side_effect = RuntimeError("db down")
            response = client.get("/ready")
        assert response.status_code == 503
        assert response.json()["status"] == "not-ready"

    def test_health_stays_available_as_before(self, client):
        # /health keeps its existing contract; /ready is the new probe.
        assert client.get("/health").status_code == 200


class TestWorkerPrincipalGC:
    @pytest.mark.asyncio
    async def test_prune_disables_principal_and_revokes_keys(self):
        server = Server(host="localhost", port=8000)
        auth_service = MagicMock()
        auth_service.revoke_all_api_keys = AsyncMock()

        principal = MagicMock()
        principal.id = "p-1"
        principal.enabled = True

        with (
            patch("flux.security.dependencies._get_auth_service", return_value=auth_service),
            patch("flux.security.principals.PrincipalRegistry") as registry_cls,
        ):
            registry = registry_cls.return_value
            registry.find.return_value = principal

            server._gc_worker_principal("dead-worker")
            await asyncio.sleep(0.05)  # let the fire-and-forget task run

        registry.set_enabled.assert_called_once_with("p-1", False)
        auth_service.revoke_all_api_keys.assert_awaited_once_with("p-1")

    @pytest.mark.asyncio
    async def test_prune_without_auth_service_is_a_noop(self):
        server = Server(host="localhost", port=8000)
        with patch("flux.security.dependencies._get_auth_service", return_value=None):
            server._gc_worker_principal("dead-worker")  # must not raise
