from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FLUX_DATABASE_URL", f"sqlite:///{tmp_path}/routes.db")
    from flux.config import Configuration

    Configuration._instance = None  # type: ignore[attr-defined]
    Configuration._config = None  # type: ignore[attr-defined]

    from flux.models import DatabaseRepository

    DatabaseRepository._engines.clear()

    # Override database_url directly so the toml value does not win.
    Configuration.get().override(database_url=f"sqlite:///{tmp_path}/routes.db")

    from flux.server import Server

    server = Server("127.0.0.1", 0)
    return TestClient(server._create_api())


def _register_source(client, source: bytes):
    files = {"file": ("flow.py", source, "text/x-python")}
    return client.post("/workflows", files=files)


def test_get_workflow_by_namespace_and_name(client):
    source = b"""
from flux import workflow

@workflow.with_options(namespace="billing")
async def invoice(ctx):
    return None
"""
    _register_source(client, source)
    resp = client.get("/workflows/billing/invoice")
    assert resp.status_code == 200
    body = resp.json()
    assert body["namespace"] == "billing"
    assert body["name"] == "invoice"


def test_legacy_bare_name_resolves_to_default(client):
    source = b"""
from flux import workflow

@workflow
async def hello(ctx):
    return None
"""
    _register_source(client, source)
    resp = client.get("/workflows/hello")
    assert resp.status_code == 200
    body = resp.json()
    assert body["namespace"] == "default"
    assert body["name"] == "hello"


def test_list_namespaces_endpoint(client):
    source = b"""
from flux import workflow

@workflow.with_options(namespace="billing")
async def invoice(ctx):
    return None

@workflow.with_options(namespace="analytics")
async def report(ctx):
    return None
"""
    _register_source(client, source)
    resp = client.get("/namespaces")
    assert resp.status_code == 200
    body = resp.json()
    namespaces = {n["namespace"] for n in body}
    assert "billing" in namespaces
    assert "analytics" in namespaces


def test_list_workflows_namespace_filter(client):
    source = b"""
from flux import workflow

@workflow.with_options(namespace="billing")
async def invoice(ctx):
    return None

@workflow.with_options(namespace="analytics")
async def report(ctx):
    return None
"""
    _register_source(client, source)
    resp = client.get("/workflows", params={"namespace": "billing"})
    assert resp.status_code == 200
    body = resp.json()
    assert all(w["namespace"] == "billing" for w in body)
    assert len(body) >= 1


def test_executions_scoped_by_namespace(client):
    """Same short name in two namespaces must not cross-pollute execution history."""
    source_billing = b"""
from flux import workflow

@workflow.with_options(namespace="billing")
async def process(ctx):
    return "billing_result"
"""
    source_analytics = b"""
from flux import workflow

@workflow.with_options(namespace="analytics")
async def process(ctx):
    return "analytics_result"
"""
    _register_source(client, source_billing)
    _register_source(client, source_analytics)

    r_billing = client.post("/workflows/billing/process/run/async", json=None)
    assert r_billing.status_code == 200, r_billing.text
    r_analytics = client.post("/workflows/analytics/process/run/async", json=None)
    assert r_analytics.status_code == 200, r_analytics.text

    billing_execs = client.get("/workflows/billing/process/executions")
    assert billing_execs.status_code == 200
    analytics_execs = client.get("/workflows/analytics/process/executions")
    assert analytics_execs.status_code == 200

    b_body = billing_execs.json()
    a_body = analytics_execs.json()
    b_total = b_body.get("total") if isinstance(b_body, dict) else len(b_body)
    a_total = a_body.get("total") if isinstance(a_body, dict) else len(a_body)
    assert b_total == 1, f"billing namespace returned {b_total} executions, expected 1"
    assert a_total == 1, f"analytics namespace returned {a_total} executions, expected 1"


def test_executions_list_endpoint_namespace_filter(client):
    """GET /executions?namespace=billing must filter server-side."""
    src_billing = b"""
from flux import workflow

@workflow.with_options(namespace="billing")
async def process(ctx):
    return "billing"
"""
    src_analytics = b"""
from flux import workflow

@workflow.with_options(namespace="analytics")
async def process(ctx):
    return "analytics"
"""
    _register_source(client, src_billing)
    _register_source(client, src_analytics)

    r_b = client.post("/workflows/billing/process/run/async", json=None)
    assert r_b.status_code == 200, r_b.text
    r_a = client.post("/workflows/analytics/process/run/async", json=None)
    assert r_a.status_code == 200, r_a.text

    r = client.get("/executions", params={"namespace": "billing"})
    assert r.status_code == 200
    body = r.json()
    executions = body.get("executions", body) if isinstance(body, dict) else body
    assert len(executions) >= 1, "expected at least one billing execution"
    for e in executions:
        assert e.get("workflow_namespace") == "billing", e

    r = client.get("/executions", params={"namespace": "analytics"})
    assert r.status_code == 200
    body = r.json()
    executions = body.get("executions", body) if isinstance(body, dict) else body
    assert len(executions) >= 1, "expected at least one analytics execution"
    for e in executions:
        assert e.get("workflow_namespace") == "analytics", e


def test_executions_list_with_workflow_name_and_namespace(client):
    """Filtering by both workflow_name and namespace must return only that workflow's executions."""
    src = b"""
from flux import workflow

@workflow.with_options(namespace="billing")
async def process(ctx):
    return "billing-p"

@workflow.with_options(namespace="billing")
async def other(ctx):
    return "billing-o"
"""
    _register_source(client, src)
    client.post("/workflows/billing/process/run/async", json=None)
    client.post("/workflows/billing/other/run/async", json=None)

    r = client.get("/executions", params={"namespace": "billing", "workflow_name": "process"})
    assert r.status_code == 200
    body = r.json()
    executions = body.get("executions", body) if isinstance(body, dict) else body
    assert len(executions) >= 1, "expected at least one billing/process execution"
    for e in executions:
        assert e.get("workflow_name") == "process", e
        assert e.get("workflow_namespace") == "billing", e
