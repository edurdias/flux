from __future__ import annotations

import json

import pytest
from starlette.testclient import TestClient

from flux.service_proxy import create_standalone_app

UNREACHABLE_SERVER = "http://127.0.0.1:1"

MCP_ACCEPT = "application/json, text/event-stream"


class TestHealthRoute:
    def test_health_without_mcp(self):
        app = create_standalone_app("test-svc", UNREACHABLE_SERVER)
        with TestClient(app, raise_server_exceptions=False) as client:
            r = client.get("/health")
            assert r.status_code == 200
            body = r.json()
            assert body["service"] == "test-svc"
            assert body["mcp_enabled"] is False

    def test_health_with_mcp(self):
        app = create_standalone_app("test-svc", UNREACHABLE_SERVER, enable_mcp=True)
        with TestClient(app, raise_server_exceptions=False) as client:
            r = client.get("/health")
            assert r.status_code == 200
            body = r.json()
            assert body["mcp_enabled"] is True


class TestWorkflowRoute:
    """Workflow routes hit the proxy handler (which fails to connect to the
    unreachable backend, producing 500/502).  The key assertion is that these
    paths do NOT return an MCP-style response."""

    def test_post_workflow_reaches_handler(self):
        app = create_standalone_app("test-svc", UNREACHABLE_SERVER)
        with TestClient(app, raise_server_exceptions=False) as client:
            r = client.post("/run/invoice")
            assert r.status_code in (404, 500, 502)

    def test_resume_workflow_reaches_handler(self):
        app = create_standalone_app("test-svc", UNREACHABLE_SERVER)
        with TestClient(app, raise_server_exceptions=False) as client:
            r = client.post("/run/invoice/resume/exec-123")
            assert r.status_code in (404, 500, 502)

    def test_status_workflow_reaches_handler(self):
        app = create_standalone_app("test-svc", UNREACHABLE_SERVER)
        with TestClient(app, raise_server_exceptions=False) as client:
            r = client.get("/run/invoice/status/exec-123")
            assert r.status_code in (404, 500, 502)


class TestMCPRouting:
    """Verify /mcp and /.well-known/ are routed to the FastMCP app,
    not swallowed by FastAPI's routing.

    This is the bug that the MCPRouteMiddleware prevents.
    """

    def test_mcp_not_treated_as_workflow(self):
        """POST /mcp must NOT return a 'Workflow mcp not found' error."""
        app = create_standalone_app("test-svc", UNREACHABLE_SERVER, enable_mcp=True)
        with TestClient(app, raise_server_exceptions=False) as client:
            r = client.post("/mcp")
            body_text = json.dumps(r.json()) if _is_json(r) else r.text
            assert "Workflow 'mcp' not found" not in body_text

    def test_mcp_initialize(self):
        """A valid MCP initialize request should get a JSON-RPC response."""
        app = create_standalone_app("test-svc", UNREACHABLE_SERVER, enable_mcp=True)
        with TestClient(app, raise_server_exceptions=False) as client:
            r = client.post(
                "/mcp",
                headers={"Accept": MCP_ACCEPT},
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {},
                        "clientInfo": {"name": "test", "version": "0.1"},
                    },
                },
            )
            assert r.status_code == 200
            body = _parse_sse_json(r.text)
            assert body.get("jsonrpc") == "2.0"
            assert "result" in body

    def test_well_known_not_treated_as_workflow(self):
        """GET /.well-known/... must NOT return a workflow-not-found error."""
        app = create_standalone_app("test-svc", UNREACHABLE_SERVER, enable_mcp=True)
        with TestClient(app, raise_server_exceptions=False) as client:
            r = client.get("/.well-known/oauth-protected-resource")
            body_text = json.dumps(r.json()) if _is_json(r) else r.text
            assert "Workflow" not in body_text

    def test_mcp_disabled_returns_404(self):
        """Without MCP, /mcp is not a registered route on this branch
        (workflows live under /run/{name}), so it should 404."""
        app = create_standalone_app("test-svc", UNREACHABLE_SERVER, enable_mcp=False)
        with TestClient(app, raise_server_exceptions=False) as client:
            r = client.post("/mcp")
            assert r.status_code in (404, 405)

    @pytest.mark.parametrize(
        "path",
        ["/mcp", "/mcp/", "/mcp/sse"],
        ids=["mcp-root", "mcp-trailing-slash", "mcp-sse"],
    )
    def test_mcp_subpaths_routed_correctly(self, path):
        """All /mcp/* subpaths should reach the FastMCP app."""
        app = create_standalone_app("test-svc", UNREACHABLE_SERVER, enable_mcp=True)
        with TestClient(app, raise_server_exceptions=False) as client:
            r = client.get(path)
            body_text = json.dumps(r.json()) if _is_json(r) else r.text
            assert "Workflow" not in body_text

    @pytest.mark.parametrize(
        "path",
        ["/run/mcp_billing", "/run/mcptest", "/run/mcp-workflow"],
        ids=["mcp_underscore", "mcptest", "mcp-hyphen"],
    )
    def test_mcp_prefixed_workflow_not_hijacked(self, path):
        """Workflows whose names start with 'mcp' must NOT be intercepted."""
        app = create_standalone_app("test-svc", UNREACHABLE_SERVER, enable_mcp=True)
        with TestClient(app, raise_server_exceptions=False) as client:
            r = client.post(path)
            # Reaches the workflow handler, not the MCP app.
            assert r.status_code in (404, 500, 502)


def _is_json(response) -> bool:
    return "application/json" in response.headers.get("content-type", "")


def _parse_sse_json(text: str) -> dict:
    """Extract the first JSON object from an SSE stream."""
    for line in text.splitlines():
        if line.startswith("data: "):
            return json.loads(line[len("data: ") :])
    raise ValueError(f"No SSE data line found in: {text!r}")
