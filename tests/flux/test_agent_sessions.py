"""Tests for the agent_sessions table + /agents/sessions endpoints.

Covers the linkage written on workflow run when the namespace is "agents" and
the input dict has an "agent" field, plus the read endpoints used by
`flux agent session list`.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FLUX_DATABASE_URL", f"sqlite:///{tmp_path}/agent_sessions.db")
    from flux.config import Configuration
    from flux.models import DatabaseRepository

    Configuration._instance = None  # type: ignore[attr-defined]
    Configuration._config = None  # type: ignore[attr-defined]
    DatabaseRepository._engines.clear()
    Configuration.get().override(database_url=f"sqlite:///{tmp_path}/agent_sessions.db")

    from flux.server import Server

    server = Server("127.0.0.1", 0)
    yield TestClient(server._create_api())

    Configuration._instance = None  # type: ignore[attr-defined]
    Configuration._config = None  # type: ignore[attr-defined]
    DatabaseRepository._engines.clear()


def _register_agent_chat_stub(client):
    """Register a minimal workflow in the agents namespace that accepts {agent: name}.

    We don't need the real agent_chat machinery — only that an execution lands
    with the right namespace and the right input shape so the server's
    agent_session linkage code runs.
    """
    source = b"""
from flux import workflow

@workflow.with_options(namespace="agents")
async def agent_chat(ctx):
    return {"agent": ctx.input.get("agent")}
"""
    files = {"file": ("agent_chat.py", source, "text/x-python")}
    r = client.post("/workflows", files=files)
    assert r.status_code == 200, r.text


def test_run_in_agents_namespace_records_session(client):
    _register_agent_chat_stub(client)
    r = client.post("/workflows/agents/agent_chat/run/async", json={"agent": "alice"})
    assert r.status_code == 200, r.text

    listing = client.get("/agents/sessions")
    assert listing.status_code == 200, listing.text
    body = listing.json()
    assert body["total"] == 1
    assert len(body["sessions"]) == 1
    row = body["sessions"][0]
    assert row["agent_name"] == "alice"
    assert row["workflow_namespace"] == "agents"
    assert row["workflow_name"] == "agent_chat"
    assert row["started_at"] is not None


def test_per_agent_endpoint_filters(client):
    _register_agent_chat_stub(client)
    for name in ("alice", "alice", "bob"):
        r = client.post("/workflows/agents/agent_chat/run/async", json={"agent": name})
        assert r.status_code == 200, r.text

    r = client.get("/agents/alice/sessions")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    assert all(s["agent_name"] == "alice" for s in body["sessions"])

    r = client.get("/agents/bob/sessions")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["sessions"][0]["agent_name"] == "bob"


def test_non_agents_namespace_does_not_create_session(client):
    """A workflow in another namespace, even with an `agent` field, is not a session."""
    source = b"""
from flux import workflow

@workflow.with_options(namespace="billing")
async def process(ctx):
    return None
"""
    files = {"file": ("billing.py", source, "text/x-python")}
    r = client.post("/workflows", files=files)
    assert r.status_code == 200, r.text

    r = client.post("/workflows/billing/process/run/async", json={"agent": "alice"})
    assert r.status_code == 200, r.text

    listing = client.get("/agents/sessions")
    assert listing.status_code == 200
    assert listing.json()["total"] == 0


def test_agents_namespace_without_agent_field_does_not_create_session(client):
    """The linkage requires an `agent` key — defensive against schema drift."""
    _register_agent_chat_stub(client)
    r = client.post("/workflows/agents/agent_chat/run/async", json={"other": "value"})
    assert r.status_code == 200, r.text

    listing = client.get("/agents/sessions")
    assert listing.status_code == 200
    assert listing.json()["total"] == 0


def test_invalid_state_filter_returns_400(client):
    _register_agent_chat_stub(client)
    r = client.get("/agents/sessions", params={"state": "NOT_A_REAL_STATE"})
    assert r.status_code == 400


def test_state_filter_matches(client):
    _register_agent_chat_stub(client)
    r = client.post("/workflows/agents/agent_chat/run/async", json={"agent": "alice"})
    assert r.status_code == 200, r.text

    # The execution was created but no worker has picked it up — state should be
    # one of CREATED / SCHEDULED. Either way, filtering by COMPLETED yields zero.
    r = client.get("/agents/sessions", params={"state": "COMPLETED"})
    assert r.status_code == 200
    assert r.json()["total"] == 0
