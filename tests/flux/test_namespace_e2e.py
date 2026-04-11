"""End-to-end test of workflow namespaces: register, run, verify."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FLUX_DATABASE_URL", f"sqlite:///{tmp_path}/e2e.db")
    monkeypatch.setenv("FLUX_SECURITY__AUTH__ENABLED", "false")
    from flux.config import Configuration

    Configuration._instance = None  # type: ignore[attr-defined]
    Configuration._config = None  # type: ignore[attr-defined]
    from flux.models import DatabaseRepository

    DatabaseRepository._engines.clear()

    Configuration.get().override(database_url=f"sqlite:///{tmp_path}/e2e.db")

    from flux.server import Server

    server = Server("127.0.0.1", 0)
    return TestClient(server._create_api())


def test_namespaced_workflow_full_lifecycle(client):
    """Register a namespaced workflow, run it async, verify namespace in response."""
    source = b"""
from flux import task, workflow

@task
async def add_one(x):
    return x + 1

@workflow.with_options(namespace="math")
async def incrementer(ctx):
    return await add_one(ctx.input)
"""
    files = {"file": ("math.py", source, "text/x-python")}
    r = client.post("/workflows", files=files)
    assert r.status_code == 200, r.text

    r = client.get("/namespaces")
    assert r.status_code == 200
    assert any(n["namespace"] == "math" for n in r.json())

    r = client.get("/workflows/math/incrementer")
    assert r.status_code == 200
    body = r.json()
    assert body["namespace"] == "math"
    assert body["name"] == "incrementer"

    # Use async mode — sync waits for a live worker which is not present in tests
    r = client.post("/workflows/math/incrementer/run/async", json=5)
    assert r.status_code == 200, r.text
    body = r.json()
    # The response summary carries workflow_namespace
    assert body.get("workflow_namespace") == "math"


def test_same_short_name_in_different_namespaces(client):
    """Two workflows with the same short name in different namespaces must not collide."""
    src_a = b"""
from flux import workflow

@workflow.with_options(namespace="billing")
async def process(ctx):
    return "billing"
"""
    src_b = b"""
from flux import workflow

@workflow.with_options(namespace="analytics")
async def process(ctx):
    return "analytics"
"""
    r_a = client.post("/workflows", files={"file": ("a.py", src_a, "text/x-python")})
    assert r_a.status_code == 200, r_a.text
    r_b = client.post("/workflows", files={"file": ("b.py", src_b, "text/x-python")})
    assert r_b.status_code == 200, r_b.text

    # Use async mode — sync waits for a live worker which is not present in tests
    r_a = client.post("/workflows/billing/process/run/async", json=None)
    r_b = client.post("/workflows/analytics/process/run/async", json=None)
    assert r_a.status_code == 200, r_a.text
    assert r_b.status_code == 200, r_b.text

    # Each execution is tagged with its own namespace
    assert r_a.json().get("workflow_namespace") == "billing"
    assert r_b.json().get("workflow_namespace") == "analytics"

    # Fetch each by qualified reference — they are distinct entities
    billing = client.get("/workflows/billing/process").json()
    analytics = client.get("/workflows/analytics/process").json()
    assert billing["namespace"] == "billing"
    assert analytics["namespace"] == "analytics"
    assert billing["source"] != analytics["source"]
