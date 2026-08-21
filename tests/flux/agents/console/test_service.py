"""Tests for ConsoleService, the single server client for console surfaces."""

from __future__ import annotations

import json
from unittest.mock import patch

import httpx
import pytest

from flux.agents.console.service import ApprovalRow, ConsoleService, SessionRow


def _patched_async_client(transport: httpx.MockTransport):
    """Return a context manager that patches ``httpx.AsyncClient`` to use a mock transport.

    Same house pattern as ``tests/flux/agents/test_flux_client.py::_patched_async_client``:
    ``respx`` is not installed and neither ``FluxClient`` nor ``ConsoleService`` take a
    transport parameter, so ``httpx.AsyncClient`` itself is monkeypatched.
    """
    real_cls = httpx.AsyncClient

    def factory(**kwargs):
        kwargs.pop("transport", None)
        return real_cls(transport=transport, **kwargs)

    return patch("flux.agents.flux_client.httpx.AsyncClient", factory)


@pytest.mark.asyncio
async def test_list_agents_returns_raw_list():
    service = ConsoleService(server_url="http://test", token="t")
    captured: dict = {}

    async def fake_handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json=[{"name": "coder"}, {"name": "writer"}])

    transport = httpx.MockTransport(fake_handler)
    with _patched_async_client(transport):
        result = await service.list_agents()
        await service.aclose()

    assert captured["url"] == "http://test/admin/agents"
    assert captured["auth"] == "Bearer t"
    assert result == [{"name": "coder"}, {"name": "writer"}]


@pytest.mark.asyncio
async def test_list_sessions_maps_rows_including_name():
    service = ConsoleService(server_url="http://test", token="t")
    captured: dict = {}

    async def fake_handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(
            200,
            json={
                "sessions": [
                    {
                        "execution_id": "exec-1",
                        "agent_name": "coder",
                        "state": "PAUSED",
                        "started_at": "2026-05-08T01:00:00",
                        "workflow_namespace": "agents",
                        "workflow_name": "agent_chat",
                        "current_worker": None,
                        "name": "Fix the bug",
                    },
                    {
                        "execution_id": "exec-2",
                        "agent_name": "writer",
                        "state": "RUNNING",
                        "started_at": None,
                        "workflow_namespace": "agents",
                        "workflow_name": "agent_custom_writer",
                        "current_worker": "w1",
                        "name": None,
                    },
                ],
                "total": 2,
                "limit": 50,
                "offset": 0,
            },
        )

    transport = httpx.MockTransport(fake_handler)
    with _patched_async_client(transport):
        page = await service.list_sessions()
        await service.aclose()

    assert captured["url"] == "http://test/agents/sessions"
    # The listing is one server page; the total behind it travels with the
    # rows so the rail can say when it is showing a subset (#245).
    assert page.total == 2
    assert not page.truncated
    assert page.rows == [
        SessionRow(
            execution_id="exec-1",
            agent_name="coder",
            state="PAUSED",
            name="Fix the bug",
            started_at="2026-05-08T01:00:00",
            workflow_name="agent_chat",
        ),
        SessionRow(
            execution_id="exec-2",
            agent_name="writer",
            state="RUNNING",
            name=None,
            started_at=None,
            workflow_name="agent_custom_writer",
        ),
    ]
    assert all(row.derived_title is None for row in page.rows)


@pytest.mark.asyncio
async def test_list_sessions_filters_by_agent():
    service = ConsoleService(server_url="http://test", token="t")
    captured: dict = {}

    async def fake_handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"sessions": [], "total": 0, "limit": 50, "offset": 0})

    transport = httpx.MockTransport(fake_handler)
    with _patched_async_client(transport):
        await service.list_sessions(agent="coder")
        await service.aclose()

    assert captured["url"] == "http://test/agents/sessions?agent=coder"


@pytest.mark.asyncio
async def test_list_sessions_passes_a_widened_page():
    """The "load the rest" control lives on the service passing limit
    through; total still counts the full set (#245)."""
    service = ConsoleService(server_url="http://test", token="t")
    captured: dict = {}

    async def fake_handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"sessions": [], "total": 200, "limit": 200, "offset": 0})

    transport = httpx.MockTransport(fake_handler)
    with _patched_async_client(transport):
        await service.list_sessions(limit=200)
        await service.aclose()

    assert captured["url"] == "http://test/agents/sessions?limit=200"


@pytest.mark.asyncio
async def test_list_approvals_maps_rows():
    service = ConsoleService(server_url="http://test", token="t")
    captured: dict = {}

    async def fake_handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(
            200,
            json={
                "approvals": [
                    {
                        "approval_id": "appr-1",
                        "execution_id": "exec-1",
                        "task_call_id": "deploy_42",
                        "workflow_namespace": "agents",
                        "workflow_name": "agent_chat",
                        "task_name": "deploy",
                        "status": "pending",
                        "requested_at": "2026-05-08T01:00:00",
                        "decided_at": None,
                        "approver": None,
                        "reason": None,
                        "scope": "call",
                        "target_value": "prod",
                    },
                ],
                "total": 1,
                "limit": 20,
                "offset": 0,
                "auth_filtered": False,
            },
        )

    transport = httpx.MockTransport(fake_handler)
    with _patched_async_client(transport):
        rows = await service.list_approvals()
        await service.aclose()

    assert captured["url"] == "http://test/approvals"
    assert rows == [
        ApprovalRow(
            execution_id="exec-1",
            task_call_id="deploy_42",
            task_name="deploy",
            target_value="prod",
            requested_at="2026-05-08T01:00:00",
        ),
    ]


@pytest.mark.asyncio
async def test_list_approvals_defaults_missing_optional_fields_to_none():
    service = ConsoleService(server_url="http://test", token=None)

    async def fake_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "approvals": [
                    {
                        "execution_id": "exec-1",
                        "task_call_id": "call-1",
                        "task_name": "deploy",
                    },
                ],
                "total": 1,
                "limit": 20,
                "offset": 0,
            },
        )

    transport = httpx.MockTransport(fake_handler)
    with _patched_async_client(transport):
        rows = await service.list_approvals()
        await service.aclose()

    assert rows[0].target_value is None
    assert rows[0].requested_at is None


@pytest.mark.asyncio
async def test_get_detail_requests_detailed_execution():
    service = ConsoleService(server_url="http://test", token="t")
    captured: dict = {}

    async def fake_handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"execution_id": "exec-1", "events": []})

    transport = httpx.MockTransport(fake_handler)
    with _patched_async_client(transport):
        result = await service.get_detail("exec-1")
        await service.aclose()

    assert captured["url"] == "http://test/executions/exec-1?detailed=true"
    assert result == {"execution_id": "exec-1", "events": []}


@pytest.mark.asyncio
async def test_spawn_uses_agent_chat_when_no_workflow_file():
    service = ConsoleService(server_url="http://test", token="t")
    requests: list[httpx.Request] = []

    async def fake_handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/admin/agents/coder":
            return httpx.Response(200, json={"name": "coder", "workflow_file": None})
        if "/run/stream" in request.url.path:
            return httpx.Response(
                200,
                headers={"Content-Type": "text/event-stream"},
                content=b'data: {"execution_id":"exec-1"}\n\n',
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    transport = httpx.MockTransport(fake_handler)
    with _patched_async_client(transport):
        execution_id = await service.spawn("coder", None)
        await service.aclose()

    assert execution_id == "exec-1"
    run_request = next(r for r in requests if "/run/stream" in r.url.path)
    assert run_request.url.path == "/workflows/agents/agent_chat/run/stream"
    # No custom-workflow registration should have happened.
    assert not any(r.method == "POST" and r.url.path == "/workflows" for r in requests)


@pytest.mark.asyncio
async def test_spawn_picks_custom_workflow_when_declared():
    """Regression: an agent declaring workflow_file must never fall back to
    the default agent_chat workflow (flux/cli.py:3057-3088's decision)."""
    service = ConsoleService(server_url="http://test", token="t")
    requests: list[httpx.Request] = []

    async def fake_handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/admin/agents/coder":
            return httpx.Response(
                200,
                json={"name": "coder", "workflow_file": "async def workflow(ctx): ..."},
            )
        if request.method == "POST" and request.url.path == "/workflows":
            return httpx.Response(200, json={"status": "ok"})
        if "/run/stream" in request.url.path:
            return httpx.Response(
                200,
                headers={"Content-Type": "text/event-stream"},
                content=b'data: {"execution_id":"exec-9"}\n\n',
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    transport = httpx.MockTransport(fake_handler)
    with _patched_async_client(transport):
        execution_id = await service.spawn("coder", "My Session")
        await service.aclose()

    assert execution_id == "exec-9"
    register_request = next(
        r for r in requests if r.method == "POST" and r.url.path == "/workflows"
    )
    assert b"agent_custom_coder" in register_request.content
    run_request = next(r for r in requests if "/run/stream" in r.url.path)
    assert run_request.url.path == "/workflows/agents/agent_custom_coder/run/stream"
    assert run_request.url.params["name"] == "My Session"


@pytest.mark.asyncio
async def test_send_resumes_with_given_workflow_name():
    service = ConsoleService(server_url="http://test", token="t")
    captured: dict = {}

    async def fake_handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            content=b'data: {"type":"TASK_PROGRESS","value":{"token":"hi"}}\n\n',
        )

    transport = httpx.MockTransport(fake_handler)
    with _patched_async_client(transport):
        events = [e async for e in service.send("exec-1", "coder", "agent_custom_coder", "hello")]
        await service.aclose()

    assert captured["url"] == "http://test/workflows/agents/agent_custom_coder/resume/exec-1/stream"
    assert captured["body"] == {"message": "hello"}
    assert events == [{"type": "TASK_PROGRESS", "value": {"token": "hi"}}]


@pytest.mark.asyncio
async def test_respond_to_elicitation_resumes_with_payload_per_session():
    """The legacy /elicitation route resumes with process-level names; the
    console must resume the caller's own execution+workflow instead."""
    service = ConsoleService(server_url="http://test", token="t")
    captured: dict = {}

    async def fake_handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            content=b'data: {"type":"TASK_COMPLETED"}\n\n',
        )

    transport = httpx.MockTransport(fake_handler)
    with _patched_async_client(transport):
        result = await service.respond_to_elicitation(
            "exec-1",
            "agent_custom_coder",
            {"elicitation_response": {"action": "accept"}},
        )
        await service.aclose()

    assert result is None
    assert captured["url"] == "http://test/workflows/agents/agent_custom_coder/resume/exec-1/stream"
    assert captured["body"] == {"elicitation_response": {"action": "accept"}}


@pytest.mark.asyncio
async def test_decide_returns_decided_on_success():
    service = ConsoleService(server_url="http://test", token="t")
    captured: dict = {}

    async def fake_handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"status": "approved"})

    transport = httpx.MockTransport(fake_handler)
    with _patched_async_client(transport):
        result = await service.decide("exec-1", "call-1", True)
        await service.aclose()

    assert result == "decided"
    assert captured["url"] == "http://test/executions/exec-1/approvals/call-1/approve"


@pytest.mark.asyncio
async def test_decide_already_decided_body_returns_already_decided():
    """decide_approval swallows the 409 and returns the body -- detection is
    keyed on body['error'], not the HTTP status code."""
    service = ConsoleService(server_url="http://test", token=None)

    async def fake_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            409,
            json={
                "error": "already_decided",
                "current_status": "approved",
                "decided_at": "2026-05-08T01:00:00",
            },
        )

    transport = httpx.MockTransport(fake_handler)
    with _patched_async_client(transport):
        result = await service.decide("exec-1", "call-1", True)
        await service.aclose()

    assert result == "already_decided"


@pytest.mark.asyncio
async def test_decide_passes_always_and_always_for_target():
    service = ConsoleService(server_url="http://test", token=None)
    captured: dict = {}

    async def fake_handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"status": "approved"})

    transport = httpx.MockTransport(fake_handler)
    with _patched_async_client(transport):
        await service.decide("exec-1", "call-1", True, always_for_target=True)
        await service.aclose()

    assert captured["body"] == {"always_for_target": True}


@pytest.mark.asyncio
async def test_rename_puts_name():
    service = ConsoleService(server_url="http://test", token="t")
    captured: dict = {}

    async def fake_handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["method"] = request.method
        captured["body"] = json.loads(request.content)
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"execution_id": "exec-1", "name": "New title"})

    transport = httpx.MockTransport(fake_handler)
    with _patched_async_client(transport):
        result = await service.rename("exec-1", "New title")
        await service.aclose()

    assert result is None
    assert captured["method"] == "PUT"
    assert captured["url"] == "http://test/executions/exec-1/name"
    assert captured["body"] == {"name": "New title"}
    assert captured["auth"] == "Bearer t"


@pytest.mark.asyncio
async def test_stop_hits_workflow_scoped_cancel_get():
    """POST /executions/{id}/cancel does not exist on the server -- stop must
    use the workflow-scoped cancel GET route."""
    service = ConsoleService(server_url="http://test", token="t")
    captured: dict = {}

    async def fake_handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["method"] = request.method
        return httpx.Response(200, json={"execution_id": "exec-1", "state": "CANCELLING"})

    transport = httpx.MockTransport(fake_handler)
    with _patched_async_client(transport):
        result = await service.stop("exec-1", "agents", "agent_custom_coder")
        await service.aclose()

    assert result is None
    assert captured["method"] == "GET"
    assert captured["url"] == "http://test/workflows/agents/agent_custom_coder/cancel/exec-1"


@pytest.mark.asyncio
async def test_aclose_is_idempotent():
    service = ConsoleService(server_url="http://test", token=None)
    await service.aclose()
    await service.aclose()


@pytest.mark.asyncio
async def test_spawn_raises_when_the_custom_workflow_fails_to_register():
    """A failed registration stops the spawn instead of running the session
    under the wrong workflow.

    This is a deliberate divergence from `flux agent start`, which logs the
    failure and carries on: the CLI's fallback lands the session on
    agent_chat, which is the shared workflow, not the one the agent
    declared. Documented in ConsoleService.spawn and, until now, pinned by
    nothing (#245).
    """
    service = ConsoleService(server_url="http://test", token="t")
    requests: list[httpx.Request] = []

    async def fake_handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/admin/agents/coder":
            return httpx.Response(
                200,
                json={"name": "coder", "workflow_file": "async def workflow(ctx): ..."},
            )
        if request.method == "POST" and request.url.path == "/workflows":
            return httpx.Response(403, json={"detail": "Permission denied"})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    transport = httpx.MockTransport(fake_handler)
    with _patched_async_client(transport):
        with pytest.raises(httpx.HTTPStatusError):
            await service.spawn("coder", None)
        await service.aclose()

    # No session was started at all -- not one on the shared workflow.
    assert not any("/run/stream" in r.url.path for r in requests)


@pytest.mark.asyncio
async def test_spawn_raises_when_the_server_reports_no_execution_id():
    """A stream that ends without an execution_id is a failed spawn, not a
    session the console can render."""
    service = ConsoleService(server_url="http://test", token="t")

    async def fake_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/admin/agents/coder":
            return httpx.Response(200, json={"name": "coder", "workflow_file": None})
        if "/run/stream" in request.url.path:
            return httpx.Response(
                200,
                headers={"Content-Type": "text/event-stream"},
                content=b"data: {}\n\n",
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    transport = httpx.MockTransport(fake_handler)
    with _patched_async_client(transport):
        with pytest.raises(RuntimeError, match="never reported an execution_id"):
            await service.spawn("coder", None)
        await service.aclose()


@pytest.mark.asyncio
async def test_list_sessions_reports_a_truncated_page():
    """The server caps the listing. A rail showing fifty of two hundred
    looks exactly like a rail showing all of them unless the total comes
    back with the rows (#245)."""
    service = ConsoleService(server_url="http://test", token="t")

    async def fake_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "sessions": [
                    {
                        "execution_id": "exec-1",
                        "agent_name": "coder",
                        "state": "RUNNING",
                        "started_at": None,
                        "workflow_namespace": "agents",
                        "workflow_name": "agent_chat",
                        "current_worker": None,
                        "name": None,
                    },
                ],
                "total": 137,
                "limit": 50,
                "offset": 0,
            },
        )

    transport = httpx.MockTransport(fake_handler)
    with _patched_async_client(transport):
        page = await service.list_sessions()
        await service.aclose()

    assert len(page.rows) == 1
    assert page.total == 137
    assert page.truncated


@pytest.mark.asyncio
async def test_a_response_without_a_total_falls_back_to_the_row_count():
    """An older server (or a stubbed one) reports no total; the page must
    then claim no truncation rather than inventing one."""
    service = ConsoleService(server_url="http://test", token="t")

    async def fake_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"sessions": []})

    transport = httpx.MockTransport(fake_handler)
    with _patched_async_client(transport):
        page = await service.list_sessions()
        await service.aclose()

    assert page.total == 0
    assert not page.truncated
