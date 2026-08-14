"""Origin/CSRF hardening for the console app's state-changing routes.

All POST/PUT routes -- the new /console/* surface AND the pre-existing
/chat, /approval, /elicitation routes (retro-hardened here, since they were
drive-by-POSTable from any website before this task) -- require the custom
`X-Flux-Console` header (forces a CORS preflight) and, when an Origin header
is present, a match against the console's own origin allowlist. GETs are
exempt throughout.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from flux.agents.console.app import _request_token
from flux.agents.console.service import ConsoleService
from flux.agents.ui.api import ApiUI
from flux.agents.ui.web import WebUI

CSRF_HEADER = {"X-Flux-Console": "1"}

# WebUI answers only to its own Host (the rebinding guard); TestClient
# would otherwise send `Host: testserver`, which is what that rejects.
CONSOLE_ORIGIN = "http://127.0.0.1:8080"


def _make_api_ui(**overrides):
    kwargs = dict(
        server_url="http://flux.test",
        agent_name="coder",
        operator_token=None,
        port=8080,
        host="127.0.0.1",
    )
    kwargs.update(overrides)
    return ApiUI(**kwargs)


# ---------------------------------------------------------------------------
# POST /console/sessions -- the brief's own Step 1 scenarios
# ---------------------------------------------------------------------------


def test_console_sessions_post_without_csrf_header_rejected():
    ui = _make_api_ui()
    client = TestClient(ui.app)
    response = client.post(
        "/console/sessions",
        json={"agent": "coder"},
        headers={"Authorization": "Bearer t"},
    )
    assert response.status_code == 403


def test_console_sessions_post_with_hostile_origin_rejected():
    ui = _make_api_ui()
    client = TestClient(ui.app)
    response = client.post(
        "/console/sessions",
        json={"agent": "coder"},
        headers={
            "Authorization": "Bearer t",
            **CSRF_HEADER,
            "Origin": "https://evil.example",
        },
    )
    assert response.status_code == 403


def test_console_sessions_post_with_own_origin_passes_security_layer():
    """A same-origin request with the header clears CSRF/Origin -- the 400
    below comes from this route's own body validation (missing 'agent'),
    proving the request reached endpoint logic rather than the security
    dependency."""
    ui = _make_api_ui()
    client = TestClient(ui.app)
    response = client.post(
        "/console/sessions",
        json={},
        headers={
            "Authorization": "Bearer t",
            **CSRF_HEADER,
            "Origin": "http://127.0.0.1:8080",
        },
    )
    assert response.status_code == 400


def test_console_sessions_post_accepts_localhost_as_127_0_0_1_equivalent():
    ui = _make_api_ui()
    client = TestClient(ui.app)
    response = client.post(
        "/console/sessions",
        json={},
        headers={
            "Authorization": "Bearer t",
            **CSRF_HEADER,
            "Origin": "http://localhost:8080",
        },
    )
    assert response.status_code == 400


def test_console_sessions_post_rejects_right_host_wrong_port():
    ui = _make_api_ui()
    client = TestClient(ui.app)
    response = client.post(
        "/console/sessions",
        json={},
        headers={
            "Authorization": "Bearer t",
            **CSRF_HEADER,
            "Origin": "http://127.0.0.1:9999",
        },
    )
    assert response.status_code == 403


def test_console_sessions_get_unaffected_by_missing_csrf_header():
    """No X-Flux-Console header on a GET must never itself produce a 403 --
    whatever status comes back is downstream of a real (here: unreachable)
    server call, not the security dependency."""
    ui = _make_api_ui()
    client = TestClient(ui.app)
    response = client.get("/console/sessions", headers={"Authorization": "Bearer t"})
    assert response.status_code != 403


# ---------------------------------------------------------------------------
# Regression hardening: pre-existing /chat, /approval, /elicitation
# ---------------------------------------------------------------------------


def test_chat_without_csrf_header_now_rejected():
    ui = _make_api_ui()
    client = TestClient(ui.app)
    response = client.post(
        "/chat",
        json={"message": "hi"},
        headers={"Authorization": "Bearer t"},
    )
    assert response.status_code == 403


def test_chat_with_csrf_header_and_own_origin_clears_security_layer():
    ui = _make_api_ui()
    client = TestClient(ui.app)
    response = client.post(
        "/chat",
        json={"message": "hi"},
        headers={
            "Authorization": "Bearer t",
            **CSRF_HEADER,
            "Origin": "http://127.0.0.1:8080",
        },
    )
    # Cleared the security dependency; whatever happens next is business
    # logic (streaming against a fake server_url), never a 403.
    assert response.status_code != 403


def test_approval_without_csrf_header_now_rejected():
    ui = _make_api_ui()
    client = TestClient(ui.app)
    response = client.post(
        "/approval/deploy_1?session=exec-1",
        json={"execution_id": "exec-1", "approved": True},
        headers={"Authorization": "Bearer t"},
    )
    assert response.status_code == 403


def test_elicitation_without_csrf_header_now_rejected():
    ui = _make_api_ui()
    client = TestClient(ui.app)
    response = client.post(
        "/elicitation/el-1?session=exec-1",
        json={"elicitation_id": "el-1", "action": "accept"},
        headers={"Authorization": "Bearer t"},
    )
    assert response.status_code == 403


def test_session_get_unaffected_by_missing_csrf_header():
    ui = _make_api_ui()
    mock_client = AsyncMock()
    mock_client.get_execution = AsyncMock(return_value={"execution_id": "exec-1"})
    with patch("flux.agents.ui.api.FluxClient", return_value=mock_client):
        client = TestClient(ui.app)
        response = client.get("/session/exec-1", headers={"Authorization": "Bearer t"})
    # No CSRF gate on GETs; 401/404/whatever downstream, never 403-for-CSRF.
    assert response.status_code != 403


# ---------------------------------------------------------------------------
# Other /console/* state-changing routes carry the same gate
# ---------------------------------------------------------------------------


def test_console_send_without_csrf_header_rejected():
    ui = _make_api_ui()
    client = TestClient(ui.app)
    response = client.post(
        "/console/sessions/exec-1/send",
        json={"text": "hi"},
        headers={"Authorization": "Bearer t"},
    )
    assert response.status_code == 403


def test_console_decide_without_csrf_header_rejected():
    ui = _make_api_ui()
    client = TestClient(ui.app)
    response = client.post(
        "/console/approvals/exec-1/deploy_1",
        json={"approve": True, "always": False, "always_for_target": False},
        headers={"Authorization": "Bearer t"},
    )
    assert response.status_code == 403


def test_console_elicitation_without_csrf_header_rejected():
    ui = _make_api_ui()
    client = TestClient(ui.app)
    response = client.post(
        "/console/sessions/exec-1/elicitation",
        json={"payload": {}},
        headers={"Authorization": "Bearer t"},
    )
    assert response.status_code == 403


def test_console_rename_without_csrf_header_rejected():
    ui = _make_api_ui()
    client = TestClient(ui.app)
    response = client.put(
        "/console/sessions/exec-1/name",
        json={"name": "new name"},
        headers={"Authorization": "Bearer t"},
    )
    assert response.status_code == 403


def test_console_stop_without_csrf_header_rejected():
    ui = _make_api_ui()
    client = TestClient(ui.app)
    response = client.post(
        "/console/sessions/exec-1/stop",
        headers={"Authorization": "Bearer t"},
    )
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# WebUI inherits the same gate (CSRF applies regardless of the auth model)
# ---------------------------------------------------------------------------


def test_web_ui_chat_without_csrf_header_rejected():
    ui = WebUI(server_url="http://flux.test", agent_name="coder", operator_token="op-token")
    client = TestClient(ui.app, base_url=CONSOLE_ORIGIN)
    response = client.post("/chat", json={"message": "hi"})
    assert response.status_code == 403


def test_web_ui_console_sessions_honors_own_origin_allowlist():
    ui = WebUI(
        server_url="http://flux.test",
        agent_name="coder",
        operator_token="op-token",
        host="127.0.0.1",
        port=9090,
    )
    client = TestClient(ui.app, base_url=CONSOLE_ORIGIN)
    response = client.post(
        "/console/sessions",
        json={},
        headers={**CSRF_HEADER, "Origin": "http://127.0.0.1:9090"},
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# api-mode per-request Bearer isolation: two overlapping requests bearing
# different tokens must never observe each other's credentials, and a
# /send turn's detached background reconciliation must keep the token of
# the request that started it even after a later, unrelated request runs.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_requests_with_different_tokens_never_cross_contaminate():
    """Regression test for a real cross-request leak: interleaving two
    requests bearing different Bearer tokens against the shared console
    service must never let one observe the other's token."""
    seen: list[tuple[str | None, str | None]] = []

    class _SlowFakeService(ConsoleService):
        def __init__(self):
            super().__init__(server_url="http://test", token=None)

        async def list_sessions(self, agent=None):
            before = _request_token.get()
            # Yields control back to the event loop -- exactly the window a
            # shared mutable service.token (the pre-fix design) could be
            # clobbered by an overlapping request's own dependency.
            await asyncio.sleep(0.05)
            after = _request_token.get()
            seen.append((before, after))
            return []

    fake = _SlowFakeService()
    with patch("flux.agents.console.app._ScopedConsoleService", return_value=fake):
        ui = _make_api_ui()
        transport = httpx.ASGITransport(app=ui.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            results = await asyncio.gather(
                client.get(
                    "/console/sessions",
                    headers={"Authorization": "Bearer token-A", **CSRF_HEADER},
                ),
                client.get(
                    "/console/sessions",
                    headers={"Authorization": "Bearer token-B", **CSRF_HEADER},
                ),
            )

    assert [r.status_code for r in results] == [200, 200]
    assert len(seen) == 2
    # Each call's own view of the token must stay stable across its own
    # await point (no request ever reads a token other than its own)...
    for before, after in seen:
        assert before == after
    # ...and the two overlapping calls really did carry different tokens.
    assert {pair[0] for pair in seen} == {"token-A", "token-B"}


@pytest.mark.asyncio
async def test_send_background_reconciliation_keeps_its_own_requests_token():
    """EventHub.run_turn's end-of-turn get_detail call runs inside a
    detached background task that can execute after unrelated requests have
    already changed the shared service's effective token -- it must keep
    using the token that was active when the turn itself was created."""
    seen_tokens: list[str | None] = []

    class _TrackingFakeService(ConsoleService):
        def __init__(self):
            super().__init__(server_url="http://test", token=None)

        async def list_sessions(self, agent=None):
            return []

        async def send(self, execution_id, agent_name, workflow_name, text):
            return
            yield  # pragma: no cover -- makes this an async generator

        async def get_detail(self, execution_id):
            await asyncio.sleep(0.08)
            seen_tokens.append(_request_token.get())
            return {"execution_id": execution_id, "workflow_name": "agent_chat", "events": []}

    fake = _TrackingFakeService()
    with patch("flux.agents.console.app._ScopedConsoleService", return_value=fake):
        ui = _make_api_ui()
        transport = httpx.ASGITransport(app=ui.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            send_task = asyncio.ensure_future(
                client.post(
                    "/console/sessions/exec-1/send",
                    json={"text": "hi"},
                    headers={"Authorization": "Bearer token-A", **CSRF_HEADER},
                ),
            )
            # Give request A's own get_detail (workflow_name resolution) a
            # head start, then fire a second, unrelated request under a
            # *different* token while A's turn is still in flight.
            await asyncio.sleep(0.02)
            other_response = await client.get(
                "/console/sessions",
                headers={"Authorization": "Bearer token-B", **CSRF_HEADER},
            )
            send_response = await send_task

    assert send_response.status_code == 200
    assert other_response.status_code == 200
    # Both of request A's get_detail calls (the direct one resolving
    # workflow_name, and run_turn's own end-of-turn reconciliation, which
    # runs in a detached background task) must have seen only its own token.
    assert seen_tokens
    assert set(seen_tokens) == {"token-A"}


def test_wildcard_bind_never_allowlists_itself_and_extras_are_admitted():
    """`--host 0.0.0.0` means "listen everywhere", not "browsers present
    Origin http://0.0.0.0" -- no browser ever sends that origin, so
    allowlisting it is dead weight while the operator's real hostname 403s.
    External exposure names its origins explicitly via allowed_origins."""
    ui = _make_api_ui(host="0.0.0.0", allowed_origins=("http://console.internal:8080/",))
    allowlist = ui._origin_allowlist()
    assert "http://0.0.0.0:8080" not in allowlist
    # Loopback stays reachable for a local browser on the same box...
    assert "http://127.0.0.1:8080" in allowlist
    # ...and the explicit origin is admitted, trailing slash normalized.
    assert "http://console.internal:8080" in allowlist


def test_allowed_origin_clears_the_security_layer_end_to_end():
    ui = _make_api_ui(host="0.0.0.0", allowed_origins=("http://console.internal:8080",))
    client = TestClient(ui.app)
    response = client.post(
        "/console/sessions",
        json={},
        headers={
            "Authorization": "Bearer t",
            **CSRF_HEADER,
            "Origin": "http://console.internal:8080",
        },
    )
    # 400 is this route's own body validation (missing 'agent') -- the
    # request cleared CSRF/Origin, which would have been a 403.
    assert response.status_code == 400
