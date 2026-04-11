"""Regression test: POST /schedules with a qualified workflow ref must persist workflow_namespace."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    from flux.config import Configuration

    Configuration._instance = None  # type: ignore[attr-defined]
    Configuration._config = None  # type: ignore[attr-defined]

    from flux.models import DatabaseRepository

    DatabaseRepository._engines.clear()

    Configuration.get().override(database_url=f"sqlite:///{tmp_path}/sched_http.db")

    from flux.server import Server

    server = Server("127.0.0.1", 0)
    return TestClient(server._create_api())


def _register_source(client, source: bytes, filename: str = "flow.py"):
    files = {"file": (filename, source, "text/x-python")}
    return client.post("/workflows", files=files)


def test_post_schedule_with_qualified_ref_persists_namespace(client):
    """Posting POST /schedules with workflow_name='billing/invoice' must store
    workflow_namespace='billing', workflow_name='invoice' (not the qualified string).
    """
    source = b"""
from flux import workflow

@workflow.with_options(namespace="billing")
async def invoice(ctx):
    return "ok"
"""
    r = _register_source(client, source, "billing_invoice.py")
    assert r.status_code == 200, r.text

    sched_body = {
        "workflow_name": "billing/invoice",
        "name": "daily",
        "schedule_config": {
            "type": "cron",
            "cron_expression": "0 0 * * *",
        },
    }
    r = client.post("/schedules", json=sched_body)
    assert r.status_code in (200, 201), r.text
    body = r.json()
    assert body.get("workflow_namespace") == "billing", body
    assert body.get("workflow_name") == "invoice", body


def test_post_schedule_with_bare_name_defaults_to_default_namespace(client):
    source = b"""
from flux import workflow

@workflow
async def hello(ctx):
    return "hi"
"""
    r = _register_source(client, source, "hello.py")
    assert r.status_code == 200, r.text

    sched_body = {
        "workflow_name": "hello",
        "name": "every_minute",
        "schedule_config": {
            "type": "cron",
            "cron_expression": "* * * * *",
        },
    }
    r = client.post("/schedules", json=sched_body)
    assert r.status_code in (200, 201), r.text
    body = r.json()
    assert body.get("workflow_namespace") == "default"
    assert body.get("workflow_name") == "hello"


def test_post_schedule_cross_namespace_isolation(client):
    """Same short name in two namespaces: each schedule must bind to the correct namespace's workflow_id."""
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
    r = _register_source(client, src_billing, "b.py")
    assert r.status_code == 200, r.text
    r = _register_source(client, src_analytics, "a.py")
    assert r.status_code == 200, r.text

    r = client.post(
        "/schedules",
        json={
            "workflow_name": "billing/process",
            "name": "billing_sched",
            "schedule_config": {
                "type": "cron",
                "cron_expression": "0 * * * *",
            },
        },
    )
    assert r.status_code in (200, 201), r.text
    billing_body = r.json()

    r = client.post(
        "/schedules",
        json={
            "workflow_name": "analytics/process",
            "name": "analytics_sched",
            "schedule_config": {
                "type": "cron",
                "cron_expression": "0 * * * *",
            },
        },
    )
    assert r.status_code in (200, 201), r.text
    analytics_body = r.json()

    assert billing_body["workflow_namespace"] == "billing"
    assert analytics_body["workflow_namespace"] == "analytics"
    assert billing_body["workflow_id"] != analytics_body["workflow_id"]
