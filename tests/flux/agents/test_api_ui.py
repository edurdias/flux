"""Integration tests for ApiUI.

ApiUI's own surface is now just `/health` plus the per-request Bearer
contract; the agent API itself is the console's `/console/*` routes, mounted
identically in web and api mode (see tests/flux/agents/console/). The
single-agent `/chat`, `/elicitation`, `/approval` and `/session` routes were
removed with the console: they carried process-level agent and workflow
names, which are wrong for any session spawned from the picker.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from flux.agents.ui.api import ApiUI

# All state-changing routes require this header (Origin/CSRF hardening);
# supplied unconditionally except where a test exercises its absence.
CSRF_HEADER = {"X-Flux-Console": "1"}


def _make_ui():
    return ApiUI(
        server_url="http://flux.test",
        agent_name="coder",
        operator_token=None,
        port=8080,
    )


def test_health_endpoint():
    ui = _make_ui()
    client = TestClient(ui.app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_needs_no_bearer():
    """Readiness probes must work without a credential."""
    client = TestClient(_make_ui().app)

    assert client.get("/health").status_code == 200


@pytest.mark.parametrize(
    "path",
    ["/console/state", "/console/agents", "/console/sessions", "/console/approvals"],
)
def test_console_reads_reject_a_missing_bearer(path):
    """api mode's defining contract: every request carries its own token."""
    client = TestClient(_make_ui().app)

    assert client.get(path).status_code == 401


def test_console_writes_reject_a_missing_bearer():
    client = TestClient(_make_ui().app)

    response = client.post("/console/sessions", json={"agent": "coder"}, headers=CSRF_HEADER)

    assert response.status_code == 401


def test_empty_bearer_token_rejected():
    client = TestClient(_make_ui().app)

    response = client.get("/console/sessions", headers={"Authorization": "Bearer "})

    assert response.status_code == 401


@pytest.mark.parametrize(
    "path",
    ["/chat", "/elicitation/el-1", "/approval/task-1", "/session/exec-1"],
)
def test_retired_single_agent_routes_are_gone(path):
    """Their replacements are /console/sessions/{id}/{send,elicitation} and
    /console/approvals/{execution_id}/{task_call_id}."""
    client = TestClient(_make_ui().app)

    assert client.get(path).status_code == 404
    assert client.post(path, json={}, headers=CSRF_HEADER).status_code == 404
