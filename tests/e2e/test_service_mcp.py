"""E2E tests -- service MCP flag, schema extraction, and endpoint tests."""
from __future__ import annotations

from pathlib import Path

import httpx

from tests.e2e.conftest import E2E_SERVER_URL

FIXTURES = Path(__file__).parent / "fixtures"
SVC_NAME = "mcp_test_svc"


def _cleanup(cli):
    try:
        cli._server_ok(["service", "delete", SVC_NAME, "--yes"])
    except Exception:
        pass


def test_service_mcp_enabled_flag(cli):
    cli.register(str(FIXTURES / "service_mcp_workflow.py"))
    try:
        r = cli._server_json(
            ["service", "create", SVC_NAME, "--namespace", "mcp_test", "--mcp", "--format", "json"],
        )
        assert r["mcp_enabled"] is True

        r = cli._server_json(["service", "show", SVC_NAME, "--format", "json"])
        assert r["mcp_enabled"] is True
    finally:
        _cleanup(cli)


def test_service_mcp_disabled_returns_404(cli):
    cli.register(str(FIXTURES / "service_mcp_workflow.py"))
    try:
        cli._server_json(
            ["service", "create", SVC_NAME, "--namespace", "mcp_test", "--format", "json"],
        )

        with httpx.Client(base_url=E2E_SERVER_URL, timeout=60) as client:
            resp = client.get(f"/services/{SVC_NAME}/mcp/tools")
            assert resp.status_code == 404
    finally:
        _cleanup(cli)


def test_service_mcp_update_toggle(cli):
    cli.register(str(FIXTURES / "service_mcp_workflow.py"))
    try:
        cli._server_json(
            ["service", "create", SVC_NAME, "--namespace", "mcp_test", "--format", "json"],
        )

        cli._server_ok(["service", "update", SVC_NAME, "--mcp"])
        r = cli._server_json(["service", "show", SVC_NAME, "--format", "json"])
        assert r["mcp_enabled"] is True

        cli._server_ok(["service", "update", SVC_NAME, "--no-mcp"])
        r = cli._server_json(["service", "show", SVC_NAME, "--format", "json"])
        assert r["mcp_enabled"] is False
    finally:
        _cleanup(cli)


def test_service_endpoint_includes_schema(cli):
    cli.register(str(FIXTURES / "service_mcp_workflow.py"))
    try:
        cli._server_json(
            ["service", "create", SVC_NAME, "--namespace", "mcp_test", "--mcp", "--format", "json"],
        )

        r = cli._server_json(["service", "show", SVC_NAME, "--format", "json"])
        endpoints = {ep["name"]: ep for ep in r["endpoints"]}

        typed = endpoints["typed_greet"]
        assert typed["input_schema"] is not None
        assert "name" in typed["input_schema"]["properties"]
        assert typed["description"] == "Greet someone with a typed input."

        untyped = endpoints["untyped_add"]
        assert untyped["input_schema"] is None
        assert untyped["description"] == "Add two numbers."
    finally:
        _cleanup(cli)


def test_service_mcp_info_endpoint(cli):
    cli.register(str(FIXTURES / "service_mcp_workflow.py"))
    try:
        cli._server_json(
            ["service", "create", SVC_NAME, "--namespace", "mcp_test", "--mcp", "--format", "json"],
        )

        with httpx.Client(base_url=E2E_SERVER_URL, timeout=60) as client:
            resp = client.get(f"/services/{SVC_NAME}/mcp/tools")
            assert resp.status_code == 200
            body = resp.json()
            assert body["service"] == SVC_NAME
            assert body["mcp_enabled"] is True
            assert body["tool_count"] == 10
            assert len(body["tools"]) == 10

            tool_names = {t["name"] for t in body["tools"]}
            for wf in ("typed_greet", "untyped_add"):
                assert wf in tool_names
                assert f"{wf}_async" in tool_names
                assert f"resume_{wf}" in tool_names
                assert f"resume_{wf}_async" in tool_names
                assert f"status_{wf}" in tool_names
    finally:
        _cleanup(cli)


def test_service_mcp_tools_include_schema_info(cli):
    cli.register(str(FIXTURES / "service_mcp_workflow.py"))
    try:
        cli._server_json(
            ["service", "create", SVC_NAME, "--namespace", "mcp_test", "--mcp", "--format", "json"],
        )

        with httpx.Client(base_url=E2E_SERVER_URL, timeout=60) as client:
            resp = client.get(f"/services/{SVC_NAME}/mcp/tools")
            body = resp.json()
            tools_by_name = {t["name"]: t for t in body["tools"]}

            typed_tool = tools_by_name["typed_greet"]
            assert "Greet someone" in typed_tool["description"]
            assert typed_tool.get("input_schema") is not None
            assert "name" in typed_tool["input_schema"]["properties"]

            untyped_tool = tools_by_name["untyped_add"]
            assert "Add two numbers" in untyped_tool["description"]
    finally:
        _cleanup(cli)


def test_service_mcp_invalid_mcp_enabled_rejected(cli):
    with httpx.Client(base_url=E2E_SERVER_URL, timeout=60) as client:
        resp = client.post(
            "/services",
            json={"name": "bad_mcp_svc", "namespaces": [], "mcp_enabled": "yes"},
        )
        assert resp.status_code == 400


def test_service_mcp_dynamic_discovery(cli):
    try:
        cli._server_json(
            ["service", "create", SVC_NAME, "--namespace", "mcp_test", "--mcp", "--format", "json"],
        )

        with httpx.Client(base_url=E2E_SERVER_URL, timeout=60) as client:
            resp = client.get(f"/services/{SVC_NAME}/mcp/tools")
            assert resp.status_code == 200
            initial_count = resp.json()["tool_count"]

        cli.register(str(FIXTURES / "service_mcp_workflow.py"))

        with httpx.Client(base_url=E2E_SERVER_URL, timeout=60) as client:
            resp = client.get(f"/services/{SVC_NAME}/mcp/tools")
            assert resp.status_code == 200
            assert resp.json()["tool_count"] > initial_count
    finally:
        _cleanup(cli)


def test_service_mcp_exclusion_hides_from_tools(cli):
    cli.register(str(FIXTURES / "service_mcp_workflow.py"))
    try:
        cli._server_json(
            [
                "service",
                "create",
                SVC_NAME,
                "--namespace",
                "mcp_test",
                "--exclude",
                "mcp_test/untyped_add",
                "--mcp",
                "--format",
                "json",
            ],
        )

        with httpx.Client(base_url=E2E_SERVER_URL, timeout=60) as client:
            resp = client.get(f"/services/{SVC_NAME}/mcp/tools")
            assert resp.status_code == 200
            body = resp.json()
            tool_names = {t["name"] for t in body["tools"]}
            assert "typed_greet" in tool_names
            assert "untyped_add" not in tool_names
            assert body["tool_count"] == 5
    finally:
        _cleanup(cli)
