"""Endpoint tests for the /console/* surface, against a stubbed ConsoleService.

Follows the house pattern from test_hub.py: a ``ConsoleService`` subclass
stands in for the real HTTP-backed one so ``EventHub`` (title cache,
fan-out, turn reconciliation) is exercised for real, without touching the
network. ``flux.agents.console.app._ScopedConsoleService`` -- the one name
``mount_console_routes`` actually constructs -- is patched to return it.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

from flux.agents.console.app import _request_token
from flux.agents.console.service import ApprovalRow, ConsoleService, SessionRow
from flux.agents.ui.api import ApiUI
from flux.agents.ui.web import WebUI

CSRF = {"X-Flux-Console": "1"}

# WebUI answers only to its own Host (the rebinding guard); TestClient
# would otherwise send `Host: testserver`, which is what that rejects.
CONSOLE_ORIGIN = "http://127.0.0.1:8080"
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
        forbidden_tokens=None,
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
        # Tokens that should fail authorization on a write attempt --
        # ConsoleWriteState's can_write probe (GET /console/state) drives
        # `stop()` against a fake execution id, so this is what makes that
        # probe token-aware in tests, mirroring the real server's per-token
        # authorization boundary.
        self._forbidden_tokens = frozenset(forbidden_tokens or ())
        self.send_calls: list[tuple] = []
        self.decide_calls: list[tuple] = []
        self.rename_calls: list[tuple] = []
        self.stop_calls: list[tuple] = []
        self.respond_calls: list[tuple] = []
        self.closed = False

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
        self.send_calls.append((execution_id, text))
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
        if _request_token.get() in self._forbidden_tokens:
            raise _forbidden(
                {
                    "error": "forbidden",
                    "missing_permission": f"workflow:{namespace}:{workflow_name}:run",
                },
            )
        self.stop_calls.append((execution_id, namespace, workflow_name))

    async def aclose(self):
        self.closed = True


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
    with patch("flux.agents.console.app._ScopedConsoleService", return_value=fake):
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
    with patch("flux.agents.console.app._ScopedConsoleService", return_value=fake):
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
# GET /console/agents -- projected, never the whole definition
# ---------------------------------------------------------------------------


def test_console_agents_projects_only_what_the_pickers_show():
    """/admin/agents returns the whole agent definition -- workflow_file and
    tools_file *source*, and mcp_servers blocks that can carry credentials.
    Both pickers render name · model · description, so that is all the
    console hands a browser."""
    fake = _FakeService(
        agents=[
            {
                "name": "coder",
                "model": "anthropic/claude",
                "description": "writes code",
                "workflow_file": "import flux\n# secret-bearing source",
                "tools_file": "def tool(): ...",
                "mcp_servers": {"github": {"env": {"GITHUB_TOKEN": "ghp_supersecret"}}},
                "system_prompt": "you are…",
            },
        ],
    )
    with patch("flux.agents.console.app._ScopedConsoleService", return_value=fake):
        ui = _make_ui()
        client = TestClient(ui.app)
        response = client.get("/console/agents", headers=HEADERS)

    assert response.status_code == 200
    assert response.json() == [
        {"name": "coder", "model": "anthropic/claude", "description": "writes code"},
    ]
    assert "ghp_supersecret" not in response.text
    assert "secret-bearing source" not in response.text


# ---------------------------------------------------------------------------
# POST /console/sessions/{id}/send -- SSE stream ending at this session's log_delta
# ---------------------------------------------------------------------------


def test_console_send_streams_sse_frames_ending_at_log_delta():
    fake = _FakeService(
        send_frames=[TOKEN_FRAME, PAUSED_CHAT_FRAME],
        detail={"execution_id": "exec-1", "workflow_name": "agent_chat", "events": []},
    )
    with patch("flux.agents.console.app._ScopedConsoleService", return_value=fake):
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


def test_console_send_frames_are_addressed_to_their_session():
    """Every frame names the session it belongs to.

    The hub multiplexes every session this console process watches, and a
    turn keeps streaming after the operator switches to another session --
    without the envelope's session_id on the wire, the browser's reducer
    cannot tell a straggler from a frame for the session now on screen, and
    appends one session's reply into another's transcript.
    """
    fake = _FakeService(
        send_frames=[TOKEN_FRAME, PAUSED_CHAT_FRAME],
        detail={"execution_id": "exec-1", "workflow_name": "agent_chat", "events": []},
    )
    with patch("flux.agents.console.app._ScopedConsoleService", return_value=fake):
        ui = _make_ui()
        client = TestClient(ui.app)
        response = client.post(
            "/console/sessions/exec-1/send",
            json={"text": "hi"},
            headers=HEADERS,
        )

    payloads = _sse_payloads(response)
    assert payloads, "the turn must stream at least its log_delta"
    assert [p.get("session_id") for p in payloads] == ["exec-1"] * len(payloads)


# ---------------------------------------------------------------------------
# POST /console/approvals/{execution_id}/{task_call_id:path} -- 409/already_decided path
# ---------------------------------------------------------------------------


def test_console_decide_returns_already_decided():
    fake = _FakeService(decide_result="already_decided")
    with patch("flux.agents.console.app._ScopedConsoleService", return_value=fake):
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
    with patch("flux.agents.console.app._ScopedConsoleService", return_value=fake):
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
    with patch("flux.agents.console.app._ScopedConsoleService", return_value=fake):
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
    with patch("flux.agents.console.app._ScopedConsoleService", return_value=fake):
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
    with patch("flux.agents.console.app._ScopedConsoleService", return_value=fake):
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


def test_console_state_can_write_false_on_first_read_for_read_only_token():
    """A real probe, not a wait-for-a-real-write-to-fail heuristic: a
    read-only token must see can_write: false on its very first
    /console/state call, before it has attempted any write at all."""
    fake = _FakeService(forbidden_tokens={"readonly-token"})
    with patch("flux.agents.console.app._ScopedConsoleService", return_value=fake):
        ui = _make_ui()
        client = TestClient(ui.app)
        response = client.get(
            "/console/state",
            headers={"Authorization": "Bearer readonly-token", **CSRF},
        )

    assert response.status_code == 200
    assert response.json()["can_write"] is False
    # The probe itself must be a real dry-run write attempt, not inference
    # from some other read -- the fake's stop() is what raises the 403.
    assert fake.stop_calls == []


def test_console_state_can_write_true_on_first_read_for_writer_token():
    fake = _FakeService(forbidden_tokens=set())
    with patch("flux.agents.console.app._ScopedConsoleService", return_value=fake):
        ui = _make_ui()
        client = TestClient(ui.app)
        response = client.get(
            "/console/state",
            headers={"Authorization": "Bearer writer-token", **CSRF},
        )

    assert response.status_code == 200
    assert response.json()["can_write"] is True


def test_console_state_can_write_is_per_token_not_global():
    """A 403 observed for one token must never degrade the console for a
    different token -- ConsoleWriteState keys its cache by token."""
    fake = _FakeService(forbidden_tokens={"readonly-token"})
    with patch("flux.agents.console.app._ScopedConsoleService", return_value=fake):
        ui = _make_ui()
        client = TestClient(ui.app)

        readonly_response = client.get(
            "/console/state",
            headers={"Authorization": "Bearer readonly-token", **CSRF},
        )
        writer_response = client.get(
            "/console/state",
            headers={"Authorization": "Bearer writer-token", **CSRF},
        )
        # Re-check the read-only token stays cached false, unaffected by the
        # writer token's call in between.
        readonly_again = client.get(
            "/console/state",
            headers={"Authorization": "Bearer readonly-token", **CSRF},
        )

    assert readonly_response.json()["can_write"] is False
    assert writer_response.json()["can_write"] is True
    assert readonly_again.json()["can_write"] is False


def test_console_state_names_the_missing_permission_for_a_read_only_token():
    """The probe's denial is the only chance to learn the permission: a
    read-only console disables every write control, so no later 403 can
    arrive to name it. The answer therefore has to ride along with
    can_write, not wait for a write that will never be attempted."""
    fake = _FakeService(forbidden_tokens={"readonly-token"})
    with patch("flux.agents.console.app._ScopedConsoleService", return_value=fake):
        ui = _make_ui()
        client = TestClient(ui.app)
        response = client.get(
            "/console/state",
            headers={"Authorization": "Bearer readonly-token", **CSRF},
        )

    body = response.json()
    assert body["can_write"] is False
    assert body["missing_permission"] == "workflow:agents:agent_chat:run"


def test_console_state_missing_permission_is_null_for_a_writer():
    fake = _FakeService(forbidden_tokens=set())
    with patch("flux.agents.console.app._ScopedConsoleService", return_value=fake):
        ui = _make_ui()
        client = TestClient(ui.app)
        response = client.get(
            "/console/state",
            headers={"Authorization": "Bearer writer-token", **CSRF},
        )

    body = response.json()
    assert body["can_write"] is True
    assert body["missing_permission"] is None


def test_console_state_names_the_permission_from_a_prose_denial():
    """The cancel route the probe uses answers in prose (`Permission denied:
    requires '...'`), not the structured dict -- so parsing only the dict
    shape would leave the real deployment's tooltip unnamed."""

    class _ProseForbiddenService(_FakeService):
        async def stop(self, execution_id, namespace, workflow_name):
            request = httpx.Request("GET", "http://test/cancel")
            response = httpx.Response(
                403,
                json={"detail": "Permission denied: requires 'workflow:agents:agent_chat:run'"},
                request=request,
            )
            raise httpx.HTTPStatusError("forbidden", request=request, response=response)

    fake = _ProseForbiddenService()
    with patch("flux.agents.console.app._ScopedConsoleService", return_value=fake):
        ui = _make_ui()
        client = TestClient(ui.app)
        response = client.get("/console/state", headers=HEADERS)

    body = response.json()
    assert body["can_write"] is False
    assert body["missing_permission"] == "workflow:agents:agent_chat:run"


def test_console_state_names_the_permission_learned_from_a_denied_write():
    """A grant revoked mid-session denies a real write rather than the boot
    probe; that body names the permission too and must be recorded."""
    fake = _FakeService(
        rename_error=_forbidden(
            {"error": "forbidden", "missing_permission": "workflow:agents:agent_chat:run"},
        ),
    )
    with patch("flux.agents.console.app._ScopedConsoleService", return_value=fake):
        ui = _make_ui()
        client = TestClient(ui.app)
        assert client.get("/console/state", headers=HEADERS).json()["missing_permission"] is None
        client.put("/console/sessions/exec-1/name", json={"name": "n"}, headers=HEADERS)
        after = client.get("/console/state", headers=HEADERS)

    assert after.json()["can_write"] is False
    assert after.json()["missing_permission"] == "workflow:agents:agent_chat:run"


def test_console_state_names_the_permission_from_the_run_routes_plural_body():
    """A denied session spawn is a denied ``/workflows/.../run/stream``, and
    that route answers with ``missing_permissions`` (plural, a list) --
    workflow_routes.py's shape. Missing it degrades the console to read-only
    without ever naming what it is missing, and because every write control
    is then disabled no later 403 can arrive to name it."""
    fake = _FakeService(
        spawn_error=_forbidden(
            {
                "message": "Authorization denied",
                "missing_permissions": ["workflow:agents:agent_chat:run"],
            },
        ),
    )
    with patch("flux.agents.console.app._ScopedConsoleService", return_value=fake):
        ui = _make_ui()
        client = TestClient(ui.app)
        create = client.post("/console/sessions", json={"agent": "coder"}, headers=HEADERS)
        assert create.status_code == 403
        state = client.get("/console/state", headers=HEADERS)

    body = state.json()
    assert body["can_write"] is False
    assert body["missing_permission"] == "workflow:agents:agent_chat:run"


def test_console_state_does_not_cache_a_transient_probe_failure():
    """Degrade-open, but never cached: a network blip during the write probe
    must not leave a genuinely read-only token seeing every write control
    enabled for the rest of the process's life."""

    class _FlakyService(_FakeService):
        def __init__(self):
            super().__init__()
            self.probes = 0

        async def stop(self, execution_id, namespace, workflow_name):
            self.probes += 1
            if self.probes == 1:
                raise httpx.ConnectError("connection refused")
            raise _forbidden(
                {"error": "forbidden", "missing_permission": "workflow:agents:agent_chat:run"},
            )

    fake = _FlakyService()
    with patch("flux.agents.console.app._ScopedConsoleService", return_value=fake):
        ui = _make_ui()
        client = TestClient(ui.app)
        first = client.get("/console/state", headers=HEADERS)
        second = client.get("/console/state", headers=HEADERS)

    assert first.json()["can_write"] is True
    assert second.json()["can_write"] is False
    assert second.json()["missing_permission"] == "workflow:agents:agent_chat:run"
    assert fake.probes == 2


@pytest.mark.parametrize(
    ("detail", "expected"),
    [
        ({"missing_permission": "workflow:a:b:run"}, "workflow:a:b:run"),
        (
            {"detail": {"message": "denied", "missing_permissions": ["workflow:a:b:run"]}},
            "workflow:a:b:run",
        ),
        ({"detail": {"error": "forbidden", "missing_permission": "x:y:z"}}, "x:y:z"),
        ({"detail": "Permission denied: requires 'agent:*:read'"}, "agent:*:read"),
        ("Permission denied: requires 'execution:*:read'", "execution:*:read"),
        ({"detail": [{"msg": "requires 'a:b'"}]}, "a:b"),
        ({"detail": "Execution not found"}, None),
        (None, None),
    ],
)
def test_missing_permission_of_reads_both_denial_shapes(detail, expected):
    from flux.agents.console.app import missing_permission_of

    assert missing_permission_of(detail) == expected


def test_console_state_reports_the_initial_session():
    """``flux agent start --session <id> --mode web`` must open on that
    session: ``/console/state`` is the only place the page can learn it, and
    console.js falls back to sessions[0] whenever the field is null."""
    fake = _FakeService()
    with patch("flux.agents.console.app._ScopedConsoleService", return_value=fake):
        ui = _make_ui(session_id="exec-42")
        client = TestClient(ui.app)
        response = client.get("/console/state", headers=HEADERS)

    assert response.json()["session"] == "exec-42"


@pytest.mark.asyncio
async def test_console_send_subscribes_only_once_its_stream_is_read():
    """The unsubscribe lives in the generator's ``finally``, so a
    subscription taken in the endpoint body leaks whenever the client hangs
    up before the generator is first resumed -- a queue nobody drains that
    every later session's events keep growing."""
    from flux.agents.console import app as console_app
    from flux.agents.console.hub import EventHub

    hubs: list[EventHub] = []

    class _WatchedHub(EventHub):
        def __init__(self, service):
            super().__init__(service)
            hubs.append(self)

    fake = _FakeService(detail={"execution_id": "exec-1", "workflow_name": "agent_chat"})
    with (
        patch.object(console_app, "_ScopedConsoleService", return_value=fake),
        patch.object(console_app, "EventHub", _WatchedHub),
    ):
        ui = _make_ui()
        endpoint = next(
            route.endpoint
            for route in ui.app.routes
            if getattr(route, "path", None) == "/console/sessions/{session_id}/send"
        )
        # Exactly the "generator never entered" case: the response object is
        # built and dropped without anything iterating its body.
        await endpoint("exec-1", {"text": "hi"}, fake)

    assert hubs[0]._subscribers == []


def test_console_service_is_closed_when_the_app_shuts_down():
    """The TUI path closes its ConsoleService; the web/api path builds one
    per app and must close it too, or the connection pool outlives the
    server."""
    fake = _FakeService()
    with patch("flux.agents.console.app._ScopedConsoleService", return_value=fake):
        ui = _make_ui()
        with TestClient(ui.app) as client:
            assert client.get("/console/state", headers=HEADERS).status_code == 200

    assert fake.closed is True


@pytest.mark.asyncio
async def test_scoped_service_reuses_one_client_per_token():
    """One ``spawn`` reads ``.client`` several times; rebuilding a FluxClient
    per read is waste. The cache is keyed by the request's own token so it
    can never hand one request's credentials to another."""
    from flux.agents.console.app import _ScopedConsoleService

    service = _ScopedConsoleService("http://flux.test")

    reset = _request_token.set("token-a")
    first = service.client
    assert service.client is first
    assert first._token == "token-a"

    _request_token.reset(reset)
    _request_token.set("token-b")
    second = service.client
    assert second is not first
    assert second._token == "token-b"


def test_console_state_reports_agent_and_server_url():
    fake = _FakeService()
    with patch("flux.agents.console.app._ScopedConsoleService", return_value=fake):
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


def test_console_state_reports_agent_none_when_no_bound_agent():
    fake = _FakeService()
    with patch("flux.agents.console.app._ScopedConsoleService", return_value=fake):
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
    client = TestClient(ui.app, base_url=CONSOLE_ORIGIN)
    response = client.get("/static/console.css")
    assert response.status_code == 200


def test_console_send_is_refused_while_a_turn_is_already_running():
    """A 409 before the stream opens, rather than a second interleaved turn.

    Two turns for one session fan their frames into the same subscribers and
    race the same execution; the server's non-PAUSED rejection only catches
    it once the second turn is already streaming (#245). What a live turn
    looks like to the route is a session marked in flight on the mounted
    hub -- the refusal under a genuinely running turn is exercised against
    a real one in tests/flux/agents/console/test_hub.py.
    """
    fake = _FakeService(
        send_frames=[TOKEN_FRAME, PAUSED_CHAT_FRAME],
        detail={"execution_id": "exec-1", "workflow_name": "agent_chat", "events": []},
    )
    with patch("flux.agents.console.app._ScopedConsoleService", return_value=fake):
        ui = _make_ui()
        client = TestClient(ui.app)
        ui.app.state.console_hub._turns_in_flight.add("exec-1")

        # Bounded read: a regression here does not answer at all (the
        # stream would wait for a log_delta that the refused turn never
        # publishes), and an unbounded client turns that into a hung job.
        response = client.post(
            "/console/sessions/exec-1/send",
            json={"text": "hi"},
            headers=HEADERS,
            timeout=10,
        )

    assert response.status_code == 409
    assert "already running" in response.json()["detail"]
    # The refused request never started a turn.
    assert fake.send_calls == []


def test_console_send_runs_again_once_the_previous_turn_finished():
    fake = _FakeService(
        send_frames=[TOKEN_FRAME, PAUSED_CHAT_FRAME],
        detail={"execution_id": "exec-1", "workflow_name": "agent_chat", "events": []},
    )
    with patch("flux.agents.console.app._ScopedConsoleService", return_value=fake):
        ui = _make_ui()
        client = TestClient(ui.app)
        first = client.post(
            "/console/sessions/exec-1/send",
            json={"text": "hi"},
            headers=HEADERS,
        )
        second = client.post(
            "/console/sessions/exec-1/send",
            json={"text": "again"},
            headers=HEADERS,
        )

    assert [first.status_code, second.status_code] == [200, 200]
    assert [text for _, text in fake.send_calls] == ["hi", "again"]


def test_the_write_probe_targets_the_workflow_this_console_runs():
    """The probe is a dry-run cancel, so it checks
    `workflow:{ns}:{workflow}:run` -- of whatever workflow it names. Named
    a constant, a console started on a custom workflow probed a permission
    its operator has no reason to hold and painted itself read-only (#245).
    """
    fake = _FakeService()
    with patch("flux.agents.console.app._ScopedConsoleService", return_value=fake):
        ui = _make_ui(workflow_name="ops_console")
        client = TestClient(ui.app)
        response = client.get("/console/state", headers=HEADERS)

    assert response.status_code == 200
    assert fake.stop_calls == [("__console_can_write_probe__", "agents", "ops_console")]


def test_the_write_probe_defaults_to_agent_chat():
    fake = _FakeService()
    with patch("flux.agents.console.app._ScopedConsoleService", return_value=fake):
        ui = _make_ui()
        client = TestClient(ui.app)
        client.get("/console/state", headers=HEADERS)

    assert fake.stop_calls == [("__console_can_write_probe__", "agents", "agent_chat")]
