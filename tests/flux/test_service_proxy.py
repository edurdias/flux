"""Unit tests for the standalone service proxy app routing."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from flux.service_proxy import create_standalone_app


@pytest.fixture
def _patch_proxy():
    """Patch the HTTP client so no real server is needed."""
    with patch("flux.service_proxy.StandaloneServiceProxy._refresh_cache", new_callable=AsyncMock):
        yield


@pytest.fixture
def app_no_mcp(_patch_proxy):
    return create_standalone_app("test-svc", "http://fake:9999", enable_mcp=False)


@pytest.fixture
def client_no_mcp(app_no_mcp):
    return TestClient(app_no_mcp, raise_server_exceptions=False)


class TestRoutingWithoutMCP:
    def test_health(self, client_no_mcp):
        r = client_no_mcp.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["service"] == "test-svc"
        assert body["mcp_enabled"] is False

    def test_workflow_run_under_run_prefix(self, client_no_mcp):
        r = client_no_mcp.post("/run/greet")
        assert r.status_code in (404, 502)

    def test_root_post_no_catch_all(self, client_no_mcp):
        r = client_no_mcp.post("/mcp")
        assert r.status_code in (404, 405)

    def test_post_without_run_prefix_is_404(self, client_no_mcp):
        r = client_no_mcp.post("/greet")
        assert r.status_code in (404, 405)


class TestRoutingWithMCP:
    @pytest.fixture
    def app_with_mcp(self, _patch_proxy):
        with patch("flux.service_mcp.ProxyEndpointProvider.get_endpoints", new_callable=AsyncMock, return_value={}):
            app = create_standalone_app("test-svc", "http://fake:9999", enable_mcp=True)
            yield app

    @pytest.fixture
    def client_with_mcp(self, app_with_mcp):
        return TestClient(app_with_mcp, raise_server_exceptions=False)

    def test_health_shows_mcp_enabled(self, client_with_mcp):
        r = client_with_mcp.get("/health")
        assert r.status_code == 200
        assert r.json()["mcp_enabled"] is True

    def test_post_mcp_not_intercepted_by_catch_all(self, client_with_mcp):
        r = client_with_mcp.post("/mcp", content=b"", headers={"content-type": "application/json"})
        assert r.status_code != 200 or r.json().get("handler") != "catch_all"

    def test_workflow_run_still_works(self, client_with_mcp):
        r = client_with_mcp.post("/run/greet")
        assert r.status_code in (404, 502)

    def test_workflow_resume_still_works(self, client_with_mcp):
        r = client_with_mcp.post("/run/greet/resume/exec-1")
        assert r.status_code in (404, 502)

    def test_workflow_status_still_works(self, client_with_mcp):
        r = client_with_mcp.get("/run/greet/status/exec-1")
        assert r.status_code in (404, 502)

    def test_bare_workflow_name_not_routed(self, client_with_mcp):
        r = client_with_mcp.post("/greet")
        assert r.status_code in (404, 405)


class TestMCPAndWorkflowCoexistence:
    """Regression tests: MCP mount and workflow routes must coexist without
    interfering with each other. These reproduce the original bug where
    ``POST /mcp`` was intercepted by a ``/{workflow_name}`` catch-all."""

    @pytest.fixture
    def app(self, _patch_proxy):
        with patch(
            "flux.service_mcp.ProxyEndpointProvider.get_endpoints",
            new_callable=AsyncMock,
            return_value={},
        ):
            yield create_standalone_app("test-svc", "http://fake:9999", enable_mcp=True)

    @pytest.fixture
    def client(self, app):
        return TestClient(app, raise_server_exceptions=False)

    def test_post_mcp_does_not_hit_workflow_handler(self, client):
        """POST /mcp must reach the MCP sub-app, not the workflow catch-all."""
        r = client.post("/mcp")
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        assert body.get("detail") != "Workflow 'mcp' not found in service 'test-svc'"

    def test_post_mcp_trailing_slash(self, client):
        r = client.post("/mcp/")
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        assert body.get("detail") != "Workflow 'mcp' not found in service 'test-svc'"

    def test_get_mcp_does_not_hit_workflow_handler(self, client):
        r = client.get("/mcp")
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        assert "workflow" not in str(body).lower() or "mcp" not in body.get("detail", "")

    def test_health_unaffected_by_mcp_mount(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["mcp_enabled"] is True

    def test_workflow_run_unaffected_by_mcp_mount(self, client):
        r = client.post("/run/my_workflow")
        assert r.status_code in (404, 502)

    def test_workflow_resume_unaffected_by_mcp_mount(self, client):
        r = client.post("/run/my_workflow/resume/exec-42")
        assert r.status_code in (404, 502)

    def test_workflow_status_unaffected_by_mcp_mount(self, client):
        r = client.get("/run/my_workflow/status/exec-42")
        assert r.status_code in (404, 502)

    def test_reserved_paths_not_routable_as_workflows(self, client):
        """Paths like /mcp, /health should never be treated as workflow names."""
        for path in ["/mcp", "/health"]:
            r = client.post(path)
            body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            assert "not found in service" not in body.get("detail", "")

    def test_multiple_workflow_paths_work(self, client):
        for name in ["deploy", "build", "test_suite"]:
            r = client.post(f"/run/{name}")
            assert r.status_code in (404, 502), f"/run/{name} returned {r.status_code}"
