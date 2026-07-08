"""Route-level tests for worker self-health on the server side.

The worker reports ``{"healthy": bool}`` on its pong; the server tracks the
unhealthy set, surfaces it in GET /workers, and clears the flag on
reconnect/disconnect. Worker-side detection is covered in
test_worker_health.py; these tests pin the server's bookkeeping.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from flux.server import Server
from flux.worker_registry import WorkerInfo


@pytest.fixture
def server_instance():
    return Server(host="localhost", port=8000)


@pytest.fixture
def test_client(server_instance):
    """Auth/identity/heartbeat stubbed: tests focus on health bookkeeping."""
    from flux.security.identity import FluxIdentity

    worker_identity = FluxIdentity(subject="w1", roles=frozenset({"worker"}))
    mock_auth = MagicMock()

    async def mock_authenticate(token):
        return worker_identity

    async def mock_is_authorized(identity, permission):
        return True

    mock_auth.authenticate = mock_authenticate
    mock_auth.is_authorized = mock_is_authorized

    with (
        patch.object(server_instance, "_verify_worker_identity"),
        patch.object(server_instance, "_record_heartbeat", new=AsyncMock()),
        patch("flux.security.dependencies._get_auth_service", return_value=mock_auth),
    ):
        yield TestClient(server_instance._create_api())


class TestPongHealthBookkeeping:
    def test_unhealthy_pong_adds_worker_to_unhealthy_set(self, test_client, server_instance):
        resp = test_client.post("/workers/w1/pong", json={"healthy": False})

        assert resp.status_code == 200
        assert "w1" in server_instance._worker_unhealthy

    def test_healthy_pong_clears_the_flag(self, test_client, server_instance):
        server_instance._worker_unhealthy.add("w1")

        resp = test_client.post("/workers/w1/pong", json={"healthy": True})

        assert resp.status_code == 200
        assert "w1" not in server_instance._worker_unhealthy

    def test_legacy_pong_without_body_counts_as_healthy(self, test_client, server_instance):
        server_instance._worker_unhealthy.add("w1")

        resp = test_client.post("/workers/w1/pong")

        assert resp.status_code == 200
        assert "w1" not in server_instance._worker_unhealthy

    def test_pong_body_without_healthy_key_counts_as_healthy(self, test_client, server_instance):
        server_instance._worker_unhealthy.add("w1")

        resp = test_client.post("/workers/w1/pong", json={})

        assert resp.status_code == 200
        assert "w1" not in server_instance._worker_unhealthy

    def test_repeated_unhealthy_pongs_are_idempotent(self, test_client, server_instance):
        test_client.post("/workers/w1/pong", json={"healthy": False})
        resp = test_client.post("/workers/w1/pong", json={"healthy": False})

        assert resp.status_code == 200
        assert server_instance._worker_unhealthy == {"w1"}


class TestWorkersListStatus:
    def _list(self, test_client, params: str = ""):
        registry = MagicMock()
        registry.list.return_value = [WorkerInfo(name="w1")]
        with patch(
            "flux.api.worker_routes.WorkerRegistry.create",
            return_value=registry,
        ):
            return test_client.get(f"/workers{params}")

    def test_connected_unhealthy_worker_reports_unhealthy(self, test_client, server_instance):
        server_instance._worker_names.append("w1")
        server_instance._worker_unhealthy.add("w1")

        resp = self._list(test_client)

        assert resp.status_code == 200
        (worker,) = resp.json()
        assert worker["status"] == "unhealthy"

    def test_connected_healthy_worker_reports_online(self, test_client, server_instance):
        server_instance._worker_names.append("w1")

        resp = self._list(test_client)

        assert resp.status_code == 200
        (worker,) = resp.json()
        assert worker["status"] == "online"

    def test_online_filter_excludes_unhealthy_worker(self, test_client, server_instance):
        server_instance._worker_names.append("w1")
        server_instance._worker_unhealthy.add("w1")

        resp = self._list(test_client, "?status=online")

        assert resp.status_code == 200
        assert resp.json() == []


class TestUnhealthyFlagCleanup:
    def test_disconnect_clears_flag_even_when_not_in_connected_set(self, server_instance):
        # Regression: the flag must not linger for a worker that was flagged
        # after its SSE teardown already removed it from _worker_names.
        server_instance._worker_unhealthy.add("w1")
        assert "w1" not in server_instance._worker_names

        server_instance._disconnect_worker("w1")

        assert "w1" not in server_instance._worker_unhealthy

    def test_disconnect_clears_flag_for_connected_worker(self, server_instance):
        server_instance._worker_names.append("w1")
        server_instance._worker_unhealthy.add("w1")

        server_instance._disconnect_worker("w1")

        assert "w1" not in server_instance._worker_names
        assert "w1" not in server_instance._worker_unhealthy
