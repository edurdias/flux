"""Integration tests for WebUI."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from flux.agents.events import AgentEvent
from flux.agents.ui.web import WebUI


@pytest.fixture(autouse=True)
def _reset_sse_app_status():
    """sse_starlette caches an anyio.Event on the first event loop it sees;
    TestClient creates a fresh loop per test, so we reset between tests."""
    from sse_starlette.sse import AppStatus

    AppStatus.should_exit_event = None
    yield
    AppStatus.should_exit_event = None


def _make_ui(token="op-token"):
    return WebUI(
        server_url="http://flux.test",
        agent_name="coder",
        operator_token=token,
        port=8080,
    )


def _mock_session_start(events):
    async def _start(self):
        self.session_id = "exec-1"
        for event in events:
            yield event

    return _start


def test_index_served_at_root():
    ui = _make_ui()
    client = TestClient(ui.app)
    response = client.get("/")
    assert response.status_code == 200
    assert "Flux Agent" in response.text
    assert "</html>" in response.text
    assert '<body data-agent-name="coder">' in response.text


def test_index_escapes_agent_name_to_prevent_xss():
    ui = WebUI(
        server_url="http://flux.test",
        agent_name='"><script>alert(1)</script>',
        operator_token="op-token",
        port=8080,
    )
    client = TestClient(ui.app)
    response = client.get("/")
    assert response.status_code == 200
    assert "<script>alert(1)</script>" not in response.text
    assert (
        '<body data-agent-name="&quot;&gt;&lt;script&gt;alert(1)&lt;/script&gt;">' in response.text
    )


def test_chat_without_operator_token_passes_through():
    """When no operator token is set, auth is disabled and requests pass through."""
    ui = _make_ui(token=None)
    client = TestClient(ui.app)
    response = client.post("/chat", json={"message": "hi"})
    assert response.status_code == 200


def test_chat_uses_operator_token_automatically():
    ui = _make_ui(token="op-token")
    events = [
        AgentEvent(kind="session_id", data={"id": "exec-1"}),
        AgentEvent(kind="chat_response", data={"content": "hi", "turn": 1}),
    ]
    with patch("flux.agents.session.AgentSession.start", _mock_session_start(events)):
        client = TestClient(ui.app)
        response = client.post("/chat", json={"message": ""})
    assert response.status_code == 200
    payloads = [
        json.loads(line[len("data: ") :])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert any(p.get("type") == "session_id" for p in payloads)
    assert any(p.get("type") == "response" for p in payloads)


def test_health_ok_without_header():
    ui = _make_ui()
    client = TestClient(ui.app)
    response = client.get("/health")
    assert response.status_code == 200


def test_health_public_even_without_operator_token():
    ui = _make_ui(token=None)
    client = TestClient(ui.app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
