"""E2E tests -- workflow service lifecycle and endpoint execution."""
from __future__ import annotations

from pathlib import Path

import httpx

from tests.e2e.conftest import E2E_SERVER_URL

FIXTURES = Path(__file__).parent / "fixtures"
SVC_NAME = "test_svc"


def _cleanup_service(cli, name=SVC_NAME):
    try:
        cli._server_ok(["service", "delete", name, "--yes"])
    except Exception:
        pass


def test_service_crud_lifecycle(cli):
    cli.register(str(FIXTURES / "service_a_workflow.py"))
    try:
        r = cli._server_json(
            ["service", "create", SVC_NAME, "--namespace", "svc_test", "--format", "json"],
        )
        assert r["name"] == SVC_NAME
        assert "svc_test" in r["namespaces"]

        r = cli._server_json(["service", "show", SVC_NAME, "--format", "json"])
        assert r["name"] == SVC_NAME
        assert "endpoints" in r

        services = cli._server_json(["service", "list", "--format", "json"])
        names = [s["name"] for s in services]
        assert SVC_NAME in names

        cli._server_ok(["service", "delete", SVC_NAME, "--yes"])

        services = cli._server_json(["service", "list", "--format", "json"])
        names = [s["name"] for s in services]
        assert SVC_NAME not in names
    finally:
        _cleanup_service(cli)


def test_service_add_remove(cli):
    cli.register(str(FIXTURES / "service_a_workflow.py"))
    cli.register(str(FIXTURES / "service_b_workflow.py"))
    try:
        cli._server_json(
            ["service", "create", SVC_NAME, "--namespace", "svc_test", "--format", "json"],
        )

        cli._server_ok(
            ["service", "add", SVC_NAME, "--workflow", "svc_other/multiply"],
        )
        r = cli._server_json(["service", "show", SVC_NAME, "--format", "json"])
        endpoint_names = [ep["name"] for ep in r.get("endpoints", [])]
        assert "multiply" in endpoint_names

        cli._server_ok(["service", "exclude", SVC_NAME, "svc_test/add"])
        r = cli._server_json(["service", "show", SVC_NAME, "--format", "json"])
        endpoint_names = [ep["name"] for ep in r.get("endpoints", [])]
        assert "add" not in endpoint_names

        cli._server_ok(["service", "include", SVC_NAME, "svc_test/add"])
        r = cli._server_json(["service", "show", SVC_NAME, "--format", "json"])
        endpoint_names = [ep["name"] for ep in r.get("endpoints", [])]
        assert "add" in endpoint_names
    finally:
        _cleanup_service(cli)


def test_service_endpoint_sync(cli):
    cli.register(str(FIXTURES / "service_a_workflow.py"))
    try:
        cli._server_json(
            ["service", "create", SVC_NAME, "--namespace", "svc_test", "--format", "json"],
        )

        with httpx.Client(base_url=E2E_SERVER_URL, timeout=60) as client:
            resp = client.post(f"/services/{SVC_NAME}/greet/sync", json="Alice")
            assert resp.status_code == 200
            assert resp.json()["message"] == "Hello, Alice"

            resp = client.post(
                f"/services/{SVC_NAME}/add/sync",
                json={"a": 3, "b": 7},
            )
            assert resp.status_code == 200
            assert resp.json()["result"] == 10
    finally:
        _cleanup_service(cli)


def test_service_endpoint_detailed(cli):
    cli.register(str(FIXTURES / "service_a_workflow.py"))
    try:
        cli._server_json(
            ["service", "create", SVC_NAME, "--namespace", "svc_test", "--format", "json"],
        )

        with httpx.Client(base_url=E2E_SERVER_URL, timeout=60) as client:
            resp = client.post(
                f"/services/{SVC_NAME}/greet/sync",
                params={"detailed": "true"},
                json="Bob",
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body["data"]["message"] == "Hello, Bob"
            assert "execution_id" in body
            assert body["state"] == "COMPLETED"
            assert body["workflow_namespace"] == "svc_test"
    finally:
        _cleanup_service(cli)


def test_service_endpoint_async(cli):
    cli.register(str(FIXTURES / "service_a_workflow.py"))
    try:
        cli._server_json(
            ["service", "create", SVC_NAME, "--namespace", "svc_test", "--format", "json"],
        )

        with httpx.Client(base_url=E2E_SERVER_URL, timeout=60) as client:
            resp = client.post(f"/services/{SVC_NAME}/greet", json="Carol")
            assert resp.status_code == 202
            body = resp.json()
            assert "execution_id" in body
            assert "status_url" in body
    finally:
        _cleanup_service(cli)


def test_service_endpoint_not_found(cli):
    cli.register(str(FIXTURES / "service_a_workflow.py"))
    try:
        cli._server_json(
            ["service", "create", SVC_NAME, "--namespace", "svc_test", "--format", "json"],
        )

        with httpx.Client(base_url=E2E_SERVER_URL, timeout=60) as client:
            resp = client.post(
                f"/services/{SVC_NAME}/nonexistent/sync",
                json=None,
            )
            assert resp.status_code == 404

            resp = client.post(
                "/services/no_such_service/greet/sync",
                json=None,
            )
            assert resp.status_code == 404
    finally:
        _cleanup_service(cli)


def test_service_dynamic_discovery(cli):
    dyn_svc = "dyn_discovery_svc"
    try:
        cli._server_json(
            ["service", "create", dyn_svc, "--namespace", "svc_dynamic", "--format", "json"],
        )

        with httpx.Client(base_url=E2E_SERVER_URL, timeout=60) as client:
            resp = client.post(f"/services/{dyn_svc}/dyn_hello/sync", json="Pre")
            assert resp.status_code == 404

        cli.register(str(FIXTURES / "service_dynamic_workflow.py"))

        with httpx.Client(base_url=E2E_SERVER_URL, timeout=60) as client:
            resp = client.post(f"/services/{dyn_svc}/dyn_hello/sync", json="Post")
            assert resp.status_code == 200
            assert resp.json()["message"] == "Hello, Post"
    finally:
        _cleanup_service(cli, dyn_svc)


def test_service_status_endpoint(cli):
    cli.register(str(FIXTURES / "service_a_workflow.py"))
    try:
        cli._server_json(
            ["service", "create", SVC_NAME, "--namespace", "svc_test", "--format", "json"],
        )

        with httpx.Client(base_url=E2E_SERVER_URL, timeout=60) as client:
            resp = client.post(f"/services/{SVC_NAME}/greet", json="StatusCheck")
            assert resp.status_code == 202
            body = resp.json()
            status_url = body["status_url"]

            import time

            time.sleep(5)
            resp = client.get(status_url)
            assert resp.status_code == 200
            assert resp.json()["state"] in ("COMPLETED", "RUNNING", "CREATED")
    finally:
        _cleanup_service(cli)


def test_service_workflow_deleted_returns_404(cli):
    cli.register(str(FIXTURES / "service_a_workflow.py"))
    try:
        cli._server_json(
            ["service", "create", SVC_NAME, "--namespace", "svc_test", "--format", "json"],
        )

        with httpx.Client(base_url=E2E_SERVER_URL, timeout=60) as client:
            resp = client.post(f"/services/{SVC_NAME}/greet/sync", json="Before")
            assert resp.status_code == 200

        cli.delete("svc_test/greet")

        with httpx.Client(base_url=E2E_SERVER_URL, timeout=60) as client:
            resp = client.post(f"/services/{SVC_NAME}/greet/sync", json="After")
            assert resp.status_code == 404
    finally:
        _cleanup_service(cli)


def test_service_exclusion_endpoint(cli):
    cli.register(str(FIXTURES / "service_a_workflow.py"))
    try:
        cli._server_json(
            [
                "service",
                "create",
                SVC_NAME,
                "--namespace",
                "svc_test",
                "--exclude",
                "svc_test/add",
                "--format",
                "json",
            ],
        )

        with httpx.Client(base_url=E2E_SERVER_URL, timeout=60) as client:
            resp = client.post(
                f"/services/{SVC_NAME}/add/sync",
                json={"a": 1, "b": 2},
            )
            assert resp.status_code == 404

            resp = client.post(f"/services/{SVC_NAME}/greet/sync", json="Allowed")
            assert resp.status_code == 200
            assert resp.json()["message"] == "Hello, Allowed"
    finally:
        _cleanup_service(cli)
