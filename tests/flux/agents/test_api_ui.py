"""Integration tests for ApiUI HTTP endpoints."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from flux.agents.events import AgentEvent
from flux.agents.ui.api import ApiUI


@pytest.fixture(autouse=True)
def _reset_sse_app_status():
    """sse_starlette caches an anyio.Event on the first event loop it sees;
    TestClient creates a fresh loop per test, so we reset between tests."""
    from sse_starlette.sse import AppStatus

    AppStatus.should_exit_event = None
    yield
    AppStatus.should_exit_event = None


def _make_ui():
    return ApiUI(
        server_url="http://flux.test",
        agent_name="coder",
        operator_token=None,
        port=8080,
    )


def _mock_session_start(events):
    async def _start(self):
        self.session_id = "exec-1"
        for event in events:
            yield event

    return _start


def _mock_session_send(events):
    async def _send(self, message):
        for event in events:
            yield event

    return _send


def test_health_endpoint():
    ui = _make_ui()
    client = TestClient(ui.app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_chat_rejects_missing_bearer():
    ui = _make_ui()
    client = TestClient(ui.app)
    response = client.post("/chat", json={"message": "hi"})
    assert response.status_code == 401


def test_chat_new_session_streams_sse():
    ui = _make_ui()
    events = [
        AgentEvent(kind="session_id", data={"id": "exec-1"}),
        AgentEvent(kind="token", data={"text": "hel"}),
        AgentEvent(kind="token", data={"text": "lo"}),
        AgentEvent(kind="chat_response", data={"content": "hello", "turn": 1}),
    ]
    with patch("flux.agents.session.AgentSession.start", _mock_session_start(events)):
        client = TestClient(ui.app)
        response = client.post(
            "/chat",
            json={"message": ""},
            headers={"Authorization": "Bearer t"},
        )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    lines = [line for line in response.text.splitlines() if line.startswith("data: ")]
    payloads = [json.loads(line[len("data: ") :]) for line in lines]
    kinds = [p.get("type") or p.get("kind") for p in payloads]
    assert "session_id" in kinds
    assert "token" in kinds
    assert "response" in kinds


def test_chat_resume_session_streams_sse():
    ui = _make_ui()
    events = [
        AgentEvent(kind="token", data={"text": "ok"}),
        AgentEvent(kind="chat_response", data={"content": "done", "turn": 2}),
    ]
    with patch("flux.agents.session.AgentSession.send", _mock_session_send(events)):
        client = TestClient(ui.app)
        response = client.post(
            "/chat?session=exec-1",
            json={"message": "next"},
            headers={"Authorization": "Bearer t"},
        )
    assert response.status_code == 200
    lines = [line for line in response.text.splitlines() if line.startswith("data: ")]
    payloads = [json.loads(line[len("data: ") :]) for line in lines]
    kinds = [p.get("type") for p in payloads]
    assert "token" in kinds
    assert "response" in kinds


def test_elicitation_response_streams_sse():
    ui = _make_ui()
    events = [AgentEvent(kind="token", data={"text": "resumed"})]

    async def _respond(self, payload):
        for event in events:
            yield event

    with patch("flux.agents.session.AgentSession.respond_to_elicitation", _respond):
        client = TestClient(ui.app)
        response = client.post(
            "/elicitation/el-1?session=exec-1",
            json={"elicitation_id": "el-1", "action": "accept"},
            headers={"Authorization": "Bearer t"},
        )
    assert response.status_code == 200
    assert "resumed" in response.text


@pytest.mark.parametrize("action", ["accept", "decline", "cancel"])
def test_elicitation_accepts_supported_actions(action):
    ui = _make_ui()
    captured: dict = {}

    async def _respond(self, payload):
        captured["payload"] = payload
        yield AgentEvent(kind="token", data={"text": "ok"})

    with patch("flux.agents.session.AgentSession.respond_to_elicitation", _respond):
        client = TestClient(ui.app)
        response = client.post(
            "/elicitation/el-1?session=exec-1",
            json={"elicitation_id": "el-1", "action": action},
            headers={"Authorization": "Bearer t"},
        )
    assert response.status_code == 200
    assert captured["payload"]["elicitation_response"]["action"] == action


def test_elicitation_rejects_unknown_action():
    ui = _make_ui()
    client = TestClient(ui.app)
    response = client.post(
        "/elicitation/el-1?session=exec-1",
        json={"elicitation_id": "el-1", "action": "whatever"},
        headers={"Authorization": "Bearer t"},
    )
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "accept" in detail and "decline" in detail and "cancel" in detail


def test_get_session_returns_execution_state():
    ui = _make_ui()
    mock_client = AsyncMock()
    mock_client.get_execution = AsyncMock(
        return_value={"execution_id": "exec-1", "state": "PAUSED"},
    )

    with patch("flux.agents.ui.api.FluxClient", return_value=mock_client):
        client = TestClient(ui.app)
        response = client.get(
            "/session/exec-1",
            headers={"Authorization": "Bearer t"},
        )
    assert response.status_code == 200
    assert response.json()["state"] == "PAUSED"


def test_chat_stream_emits_error_on_exception():
    ui = _make_ui()

    async def _boom(self):
        raise RuntimeError("flux exploded")
        yield  # unreachable

    with patch("flux.agents.session.AgentSession.start", _boom):
        client = TestClient(ui.app)
        response = client.post(
            "/chat",
            json={"message": ""},
            headers={"Authorization": "Bearer t"},
        )
    assert response.status_code == 200
    payloads = [
        json.loads(line[len("data: ") :])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    error_frames = [p for p in payloads if p.get("type") == "error"]
    assert len(error_frames) == 1
    assert "flux exploded" in error_frames[0]["message"]


def test_elicitation_stream_emits_error_on_exception():
    ui = _make_ui()

    async def _boom(self, payload):
        raise RuntimeError("resume failed")
        yield  # unreachable

    with patch("flux.agents.session.AgentSession.respond_to_elicitation", _boom):
        client = TestClient(ui.app)
        response = client.post(
            "/elicitation/el-1?session=exec-1",
            json={"elicitation_id": "el-1", "action": "accept"},
            headers={"Authorization": "Bearer t"},
        )
    assert response.status_code == 200
    payloads = [
        json.loads(line[len("data: ") :])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    error_frames = [p for p in payloads if p.get("type") == "error"]
    assert len(error_frames) == 1
    assert "resume failed" in error_frames[0]["message"]


def test_elicitation_rejects_missing_bearer():
    ui = _make_ui()
    client = TestClient(ui.app)
    response = client.post(
        "/elicitation/el-1?session=exec-1",
        json={"elicitation_id": "el-1", "action": "accept"},
    )
    assert response.status_code == 401


def test_session_rejects_missing_bearer():
    ui = _make_ui()
    client = TestClient(ui.app)
    response = client.get("/session/exec-1")
    assert response.status_code == 401


def test_empty_bearer_token_rejected():
    ui = _make_ui()
    client = TestClient(ui.app)
    response = client.get("/session/exec-1", headers={"Authorization": "Bearer   "})
    assert response.status_code == 401
