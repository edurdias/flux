"""Route-level tests for worker pause/status bookkeeping on the server side.

The worker reports ``{"status": "active"|"paused"|"draining",
"in_flight": int}`` on its pong; the server tracks the paused set (excluded
from dispatch like unhealthy workers, but surfaced as a deliberate state),
records the in-flight count, and clears both on disconnect/registration.
Worker-side behavior is covered in test_worker_control.py.
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
    """Auth/identity/heartbeat stubbed: tests focus on pause bookkeeping."""
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


class TestPongPauseBookkeeping:
    def test_paused_pong_adds_worker_to_paused_set(self, test_client, server_instance):
        resp = test_client.post(
            "/workers/w1/pong",
            json={"healthy": True, "status": "paused", "in_flight": 2},
        )

        assert resp.status_code == 200
        assert "w1" in server_instance._worker_paused
        assert server_instance._worker_in_flight["w1"] == 2

    def test_active_pong_clears_the_flag(self, test_client, server_instance):
        server_instance._worker_paused.add("w1")

        resp = test_client.post(
            "/workers/w1/pong",
            json={"healthy": True, "status": "active", "in_flight": 0},
        )

        assert resp.status_code == 200
        assert "w1" not in server_instance._worker_paused

    def test_legacy_pong_without_status_counts_as_active(self, test_client, server_instance):
        server_instance._worker_paused.add("w1")

        resp = test_client.post("/workers/w1/pong", json={"healthy": True})

        assert resp.status_code == 200
        assert "w1" not in server_instance._worker_paused

    def test_paused_is_independent_of_health(self, test_client, server_instance):
        resp = test_client.post(
            "/workers/w1/pong",
            json={"healthy": False, "status": "paused"},
        )

        assert resp.status_code == 200
        assert "w1" in server_instance._worker_paused
        assert "w1" in server_instance._worker_unhealthy

    def test_garbage_in_flight_is_ignored(self, test_client, server_instance):
        resp = test_client.post(
            "/workers/w1/pong",
            json={"healthy": True, "status": "active", "in_flight": "many"},
        )

        assert resp.status_code == 200
        assert "w1" not in server_instance._worker_in_flight

    def test_missing_in_flight_clears_stale_count(self, test_client, server_instance):
        # A worker that stops advertising in_flight (legacy or partial
        # payload) must read as "unknown", not keep its last count forever.
        server_instance._worker_in_flight["w1"] = 7

        resp = test_client.post("/workers/w1/pong", json={"healthy": True})

        assert resp.status_code == 200
        assert "w1" not in server_instance._worker_in_flight


class TestSSEDispatchGate:
    def test_paused_worker_skipped_like_unhealthy(self, server_instance):
        # The SSE claim loop consults the same gate as the dispatcher; pin
        # the membership contract the loop reads.
        server_instance._worker_paused.add("w1")
        assert "w1" in server_instance._worker_paused


class TestWorkersListStatus:
    def _list(self, test_client, params: str = ""):
        registry = MagicMock()
        registry.list.return_value = [WorkerInfo(name="w1")]
        with patch(
            "flux.api.worker_routes.WorkerRegistry.create",
            return_value=registry,
        ):
            return test_client.get(f"/workers{params}")

    def test_paused_worker_reports_paused(self, test_client, server_instance):
        server_instance._worker_names.append("w1")
        server_instance._worker_paused.add("w1")

        resp = self._list(test_client)

        assert resp.status_code == 200
        (worker,) = resp.json()
        assert worker["status"] == "paused"

    def test_paused_wins_over_unhealthy(self, test_client, server_instance):
        server_instance._worker_names.append("w1")
        server_instance._worker_paused.add("w1")
        server_instance._worker_unhealthy.add("w1")

        resp = self._list(test_client)

        (worker,) = resp.json()
        assert worker["status"] == "paused"

    def test_in_flight_surfaces_in_worker_list(self, test_client, server_instance):
        server_instance._worker_names.append("w1")
        server_instance._worker_in_flight["w1"] = 5

        resp = self._list(test_client)

        (worker,) = resp.json()
        assert worker["in_flight"] == 5

    def test_paused_filter_selects_only_paused(self, test_client, server_instance):
        server_instance._worker_names.append("w1")
        server_instance._worker_paused.add("w1")

        online = self._list(test_client, "?status=online")
        paused = self._list(test_client, "?status=paused")

        assert online.json() == []
        assert [w["name"] for w in paused.json()] == ["w1"]


class TestPausedFlagCleanup:
    def test_disconnect_clears_pause_state(self, server_instance):
        server_instance._worker_names.append("w1")
        server_instance._worker_paused.add("w1")
        server_instance._worker_in_flight["w1"] = 3

        server_instance._disconnect_worker("w1")

        assert "w1" not in server_instance._worker_paused
        assert "w1" not in server_instance._worker_in_flight
