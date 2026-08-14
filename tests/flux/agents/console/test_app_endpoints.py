"""Endpoint tests for the /console/* surface, against a stubbed ConsoleService.

Follows the house pattern from test_hub.py: a ``ConsoleService`` subclass
stands in for the real HTTP-backed one so ``EventHub`` (title cache,
fan-out, turn reconciliation) is exercised for real, without touching the
network. ``flux.agents.console.app.ConsoleService`` -- the one name
``mount_console_routes`` actually constructs -- is patched to return it.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

from flux.agents.console.service import ApprovalRow, ConsoleService, SessionRow
from flux.agents.ui.api import ApiUI
from flux.agents.ui.web import WebUI

CSRF = {"X-Flux-Console": "1"}
AUTH = {"Authorization": "Bearer t"}
HEADERS = {**AUTH, **CSRF}

TOKEN_FRAME = {"type": "TASK_PROGRESS", "value": {"token": "hi"}}
PAUSED_CHAT_FRAME = {
    "execution_id": "exec-1",
    "state": "PAUSED",
    "output": {"type": "chat_response", "content": "hello back", "turn": 1},
}


class _FakeService(ConsoleService):
    """Stands in for the real HTTP client -- subclasses the declared
    ``ConsoleService`` contract rather than a bare duck-typed double, same
    reasoning as test_hub.py's ``_FakeService``."""

    def __init__(
        self,
        sessions=None,
        approvals=None,
        agents=None,
        detail=None,
        send_frames=None,
        decide_result="decided",
        spawn_execution_id="exec-new",
        spawn_error=None,
        rename_error=None,
    ):
        super().__init__(server_url="http://test", token=None)
        self._sessions = sessions if sessions is not None else []
        self._approvals = approvals if approvals is not None else []
        self._agents = agents if agents is not None else []
        self._detail = detail if detail is not None else {"execution_id": "exec-1", "events": []}
        self._send_frames = send_frames if send_frames is not None else []
        self._decide_result = decide_result
        self._spawn_execution_id = spawn_execution_id
        self._spawn_error = spawn_error
        self._rename_error = rename_error
        self.decide_calls: list[tuple] = []
        self.rename_calls: list[tuple] = []
        self.stop_calls: list[tuple] = []
        self.respond_calls: list[tuple] = []

    async def list_agents(self):
        return self._agents

    async def list_sessions(self, agent=None):
        return self._sessions

    async def list_approvals(self):
        return self._approvals

    async def get_detail(self, execution_id):
        return self._detail

    async def spawn(self, agent_name, name):
        if self._spawn_error is not None:
            raise self._spawn_error
        return self._spawn_execution_id

    async def send(self, execution_id, agent_name, workflow_name, text):
        for frame in self._send_frames:
            yield frame

    async def respond_to_elicitation(self, execution_id, workflow_name, payload):
        self.respond_calls.append((execution_id, workflow_name, payload))

    async def decide(
        self,
        execution_id,
        task_call_id,
        approve,
        always=False,
        always_for_target=False,
    ):
        self.decide_calls.append((execution_id, task_call_id, approve, always, always_for_target))
        return self._decide_result

    async def rename(self, execution_id, name):
        if self._rename_error is not None:
            raise self._rename_error
        self.rename_calls.append((execution_id, name))

    async def stop(self, execution_id, namespace, workflow_name):
        self.stop_calls.append((execution_id, namespace, workflow_name))


def _forbidden(detail: dict) -> httpx.HTTPStatusError:
    request = httpx.Request("PUT", "http://test/x")
    response = httpx.Response(403, json=detail, request=request)
    return httpx.HTTPStatusError("forbidden", request=request, response=response)


def _make_ui(**overrides):
    kwargs = dict(server_url="http://flux.test", agent_name="coder", port=8080)
    kwargs.update(overrides)
    return ApiUI(**kwargs)


@pytest.fixture(autouse=True)
def _reset_sse_app_status():
    """sse_starlette caches an anyio.Event on the first event loop it sees;
    TestClient creates a fresh loop per test, so we reset between tests."""
    from sse_starlette.sse import AppStatus

    AppStatus.should_exit_event = None
    yield
    AppStatus.should_exit_event = None


def _sse_payloads(response) -> list[dict]:
    return [
        json.loads(line[len("data: ") :])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]


# ---------------------------------------------------------------------------
# GET /console/sessions -- name + derived_title from the hub's cache only
# ---------------------------------------------------------------------------


def test_console_sessions_carries_name_and_derived_title_from_hub_cache():
    fake = _FakeService(
        sessions=[
            SessionRow(
                execution_id="exec-opened",
                agent_name="coder",
                state="PAUSED",
                name=None,
                started_at="2026-05-08T01:00:00",
                workflow_name="agent_chat",
            ),
            SessionRow(
                execution_id="exec-unopened",
                agent_name="coder",
                state="RUNNING",
                name="custom name",
                started_at="2026-05-08T02:00:00",
                workflow_name="agent_chat",
            ),
        ],
        detail={
            "execution_id": "exec-opened",
            "workflow_name": "agent_chat",
            "events": [
                {"type": "WORKFLOW_RESUMED", "value": {"message": "Fix the flaky test please"}},
            ],
        },
    )
    with patch("flux.agents.console.app.ConsoleService", return_value=fake):
        ui = _make_ui()
        client = TestClient(ui.app)
        # Warm the hub's title cache the way the UI does: open the session.
        opened = client.get("/console/sessions/exec-opened/detail", headers=HEADERS)
        assert opened.status_code == 200

        response = client.get("/console/sessions", headers=HEADERS)

    assert response.status_code == 200
    rows = {row["execution_id"]: row for row in response.json()}
    assert rows["exec-opened"]["derived_title"] == "Fix the flaky test please"
    assert rows["exec-unopened"]["derived_title"] is None
    assert rows["exec-unopened"]["name"] == "custom name"
    assert rows["exec-opened"]["state"] == "PAUSED"
    assert rows["exec-opened"]["workflow_name"] == "agent_chat"


def test_console_approvals_lists_rows():
    fake = _FakeService(
        approvals=[
            ApprovalRow(
                execution_id="exec-1",
                task_call_id="deploy_1",
                task_name="deploy",
                target_value="prod",
                requested_at="2026-05-08T01:00:00",
            ),
        ],
    )
    with patch("flux.agents.console.app.ConsoleService", return_value=fake):
        ui = _make_ui()
        client = TestClient(ui.app)
        response = client.get("/console/approvals", headers=HEADERS)

    assert response.status_code == 200
    assert response.json() == [
        {
            "execution_id": "exec-1",
            "task_call_id": "deploy_1",
            "task_name": "deploy",
            "target_value": "prod",
            "requested_at": "2026-05-08T01:00:00",
        },
    ]


# ---------------------------------------------------------------------------
# POST /console/sessions/{id}/send -- SSE stream ending at this session's log_delta
# ---------------------------------------------------------------------------


def test_console_send_streams_sse_frames_ending_at_log_delta():
    fake = _FakeService(
        send_frames=[TOKEN_FRAME, PAUSED_CHAT_FRAME],
        detail={"execution_id": "exec-1", "workflow_name": "agent_chat", "events": []},
    )
    with patch("flux.agents.console.app.ConsoleService", return_value=fake):
        ui = _make_ui()
        client = TestClient(ui.app)
        response = client.post(
            "/console/sessions/exec-1/send",
            json={"text": "hi"},
            headers=HEADERS,
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    payloads = _sse_payloads(response)
    kinds = [p["kind"] for p in payloads]
    assert kinds == ["token", "session_id", "chat_response", "log_delta"]
    assert payloads[-1]["data"] == {"detail": fake._detail}


# ---------------------------------------------------------------------------
# POST /console/approvals/{execution_id}/{task_call_id:path} -- 409/already_decided path
# ---------------------------------------------------------------------------


def test_console_decide_returns_already_decided():
    fake = _FakeService(decide_result="already_decided")
    with patch("flux.agents.console.app.ConsoleService", return_value=fake):
        ui = _make_ui()
        client = TestClient(ui.app)
        response = client.post(
            "/console/approvals/exec-1/deploy_1",
            json={"approve": True, "always": False, "always_for_target": False},
            headers=HEADERS,
        )

    assert response.status_code == 200
    assert response.json() == {"result": "already_decided"}
    assert fake.decide_calls == [("exec-1", "deploy_1", True, False, False)]


def test_console_decide_requires_boolean_approve():
    fake = _FakeService()
    with patch("flux.agents.console.app.ConsoleService", return_value=fake):
        ui = _make_ui()
        client = TestClient(ui.app)
        response = client.post(
            "/console/approvals/exec-1/deploy_1",
            json={},
            headers=HEADERS,
        )

    assert response.status_code == 400


# ---------------------------------------------------------------------------
# POST /console/sessions -- create, and clean-error surfacing on spawn failure
# ---------------------------------------------------------------------------


def test_console_create_session_returns_execution_id():
    fake = _FakeService(spawn_execution_id="exec-brand-new")
    with patch("flux.agents.console.app.ConsoleService", return_value=fake):
        ui = _make_ui()
        client = TestClient(ui.app)
        response = client.post(
            "/console/sessions",
            json={"agent": "coder", "name": "Fix CI"},
            headers=HEADERS,
        )

    assert response.status_code == 200
    assert response.json() == {"execution_id": "exec-brand-new"}


def test_console_create_session_translates_spawn_runtime_error_to_clean_502():
    """ConsoleService.spawn raises a bare RuntimeError when the server never
    reports an execution_id for a custom-workflow registration (Task 4's
    review note) -- this must reach the client as a clean HTTPException, not
    an unhandled-exception 500 traceback."""
    fake = _FakeService(spawn_error=RuntimeError("spawn: server never reported an execution_id"))
    with patch("flux.agents.console.app.ConsoleService", return_value=fake):
        ui = _make_ui()
        client = TestClient(ui.app)
        response = client.post(
            "/console/sessions",
            json={"agent": "coder"},
            headers=HEADERS,
        )

    assert response.status_code == 502
    assert "execution_id" in response.json()["detail"]


# ---------------------------------------------------------------------------
# GET /console/state -- can_write flips on an observed structured 403
# ---------------------------------------------------------------------------


def test_console_state_can_write_flips_false_and_403_body_surfaces_verbatim():
    fake = _FakeService(
        rename_error=_forbidden(
            {"error": "forbidden", "missing_permission": "workflow:agents:agent_chat:run"},
        ),
    )
    with patch("flux.agents.console.app.ConsoleService", return_value=fake):
        ui = _make_ui()
        client = TestClient(ui.app)

        before = client.get("/console/state", headers=HEADERS)
        assert before.json()["can_write"] is True

        rename_response = client.put(
            "/console/sessions/exec-1/name",
            json={"name": "new name"},
            headers=HEADERS,
        )
        assert rename_response.status_code == 403
        assert rename_response.json()["detail"] == {
            "error": "forbidden",
            "missing_permission": "workflow:agents:agent_chat:run",
        }

        after = client.get("/console/state", headers=HEADERS)

    assert after.json()["can_write"] is False


def test_console_state_reports_agent_and_server_url():
    fake = _FakeService()
    with patch("flux.agents.console.app.ConsoleService", return_value=fake):
        ui = _make_ui(agent_name="coder")
        client = TestClient(ui.app)
        response = client.get("/console/state", headers=HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body["agent"] == "coder"
    assert body["session"] is None
    assert body["server_url"] == "http://flux.test"
    assert body["can_write"] is True


# ---------------------------------------------------------------------------
# Console mode with no bound agent (agent_name=None)
# ---------------------------------------------------------------------------


def test_chat_returns_404_when_agent_name_none():
    ui = _make_ui(agent_name=None)
    client = TestClient(ui.app)
    response = client.post("/chat", json={"message": "hi"}, headers=HEADERS)
    assert response.status_code == 404
    assert "console runs multi-session" in response.json()["detail"]


def test_console_state_reports_agent_none_when_no_bound_agent():
    fake = _FakeService()
    with patch("flux.agents.console.app.ConsoleService", return_value=fake):
        ui = _make_ui(agent_name=None)
        client = TestClient(ui.app)
        response = client.get("/console/state", headers=HEADERS)

    assert response.status_code == 200
    assert response.json()["agent"] is None


# ---------------------------------------------------------------------------
# WebUI: static mount serves the (eventually Task-7-built) web bundle
# ---------------------------------------------------------------------------


@pytest.fixture
def _throwaway_console_css():
    """Task 7 owns building the real bundle; this proves only that the
    StaticFiles mount is wired to flux/agents/web/, via a throwaway file so
    this task doesn't have to ship bundle content."""
    import flux.agents.ui.web as web_module

    web_dir = Path(web_module.__file__).parent.parent / "web"
    target = web_dir / "console.css"
    created = not target.exists()
    if created:
        target.write_text("/* placeholder for the static-mount test */")
    yield target
    if created:
        target.unlink()


def test_static_mount_serves_console_css(_throwaway_console_css):
    ui = WebUI(server_url="http://flux.test", agent_name="coder", operator_token="op-token")
    client = TestClient(ui.app)
    response = client.get("/static/console.css")
    assert response.status_code == 200
