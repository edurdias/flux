"""Origin/CSRF hardening for the console app's state-changing routes.

All POST/PUT routes -- the new /console/* surface AND the pre-existing
/chat, /approval, /elicitation routes (retro-hardened here, since they were
drive-by-POSTable from any website before this task) -- require the custom
`X-Flux-Console` header (forces a CORS preflight) and, when an Origin header
is present, a match against the console's own origin allowlist. GETs are
exempt throughout.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from flux.agents.ui.api import ApiUI
from flux.agents.ui.web import WebUI

CSRF_HEADER = {"X-Flux-Console": "1"}


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
    client = TestClient(ui.app)
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
    client = TestClient(ui.app)
    response = client.post(
        "/console/sessions",
        json={},
        headers={**CSRF_HEADER, "Origin": "http://127.0.0.1:9090"},
    )
    assert response.status_code == 400
