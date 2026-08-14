"""Integration tests for WebUI."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from flux.agents.events import AgentEvent
from flux.agents.ui.web import WebUI

# State-changing routes now require this header regardless of auth model.
CSRF_HEADER = {"X-Flux-Console": "1"}

# WebUI answers only to its own Host (rebinding guard); TestClient would
# otherwise send `Host: testserver`, which is exactly what that rejects.
CONSOLE_ORIGIN = "http://127.0.0.1:8080"


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


def test_console_shell_served_at_root():
    ui = _make_ui()
    client = TestClient(ui.app, base_url=CONSOLE_ORIGIN)
    response = client.get("/")
    assert response.status_code == 200
    assert "Flux Console" in response.text
    assert "</html>" in response.text


def test_index_never_templates_the_agent_name():
    """The retired single-agent page interpolated (and had to escape) the
    agent name; the console shell is a static file and reads the agent from
    /console/state, so no agent-controlled string reaches the served HTML."""
    ui = WebUI(
        server_url="http://flux.test",
        agent_name='"><script>alert(1)</script>',
        operator_token="op-token",
        port=8080,
    )
    client = TestClient(ui.app, base_url=CONSOLE_ORIGIN)
    response = client.get("/")
    assert response.status_code == 200
    assert "alert(1)" not in response.text


def test_chat_without_operator_token_passes_through():
    """When no operator token is set, auth is disabled and requests pass through."""
    ui = _make_ui(token=None)
    client = TestClient(ui.app, base_url=CONSOLE_ORIGIN)
    response = client.post("/chat", json={"message": "hi"}, headers=CSRF_HEADER)
    assert response.status_code == 200


def test_chat_uses_operator_token_automatically():
    ui = _make_ui(token="op-token")
    events = [
        AgentEvent(kind="session_id", data={"id": "exec-1"}),
        AgentEvent(kind="chat_response", data={"content": "hi", "turn": 1}),
    ]
    with patch("flux.agents.session.AgentSession.start", _mock_session_start(events)):
        client = TestClient(ui.app, base_url=CONSOLE_ORIGIN)
        response = client.post("/chat", json={"message": ""}, headers=CSRF_HEADER)
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
    client = TestClient(ui.app, base_url=CONSOLE_ORIGIN)
    response = client.get("/health")
    assert response.status_code == 200


def test_health_public_even_without_operator_token():
    ui = _make_ui(token=None)
    client = TestClient(ui.app, base_url=CONSOLE_ORIGIN)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


class TestHostGuard:
    """Host-header allowlist: the DNS-rebinding defense.

    A rebound name resolves to 127.0.0.1, so the browser treats the
    attacker's origin as same-origin with the console and can *read* it.
    The Origin check does not help: it only guards state-changing routes.
    """

    def _ui(self, **overrides):
        kwargs = dict(server_url="http://flux.test", agent_name="coder", port=8080)
        kwargs.update(overrides)
        return WebUI(**kwargs)

    def _client(self, **overrides):
        return TestClient(self._ui(**overrides).app)

    def test_foreign_host_is_rejected_on_reads_not_just_writes(self):
        client = self._client()

        for path in ("/", "/console/sessions", "/static/console.css"):
            response = client.get(path, headers={"Host": "attacker.example:8080"})
            assert response.status_code == 400, path
            assert "Invalid host header" in response.text

    @pytest.mark.parametrize(
        "header",
        ["127.0.0.1:8080", "localhost:8080", "localhost", "[::1]:8080"],
    )
    def test_console_answers_to_its_own_names(self, header):
        client = self._client()

        response = client.get("/", headers={"Host": header})

        assert response.status_code == 200

    def test_ipv6_loopback_bind_answers_to_its_bracketed_host(self):
        """Starlette's TrustedHostMiddleware splits the header on ':' and
        mangles this exact case, which is why the guard is hand-rolled."""
        client = self._client(host="::1")

        assert client.get("/", headers={"Host": "[::1]:8080"}).status_code == 200

    def test_deliberate_exposure_drops_the_guard(self):
        """Rebinding is a way to reach a loopback-only service through a
        victim's browser; a reachable port needs no such trick."""
        client = self._client(host="0.0.0.0", allow_remote=True)

        assert client.get("/", headers={"Host": "console.internal:8080"}).status_code == 200

    def test_exposed_console_says_so_on_startup(self):
        banner = "\n".join(self._ui(host="0.0.0.0", allow_remote=True)._startup_banner())

        assert "http://0.0.0.0:8080" in banner
        assert "no authentication of its own" in banner
        assert "proxy" in banner

    def test_loopback_console_banner_is_just_the_url(self):
        banner = "\n".join(self._ui()._startup_banner())

        assert banner == "Console: http://127.0.0.1:8080"
