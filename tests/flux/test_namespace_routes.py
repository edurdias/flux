from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FLUX_DATABASE_URL", f"sqlite:///{tmp_path}/routes.db")
    from flux.config import Configuration

    Configuration._instance = None  # type: ignore[attr-defined]
    Configuration._config = None  # type: ignore[attr-defined]

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
