"""Tests for FluxClient."""

from __future__ import annotations

import json
from unittest.mock import patch

import httpx
import pytest

from flux.agents.flux_client import FluxClient


def test_client_init():
    client = FluxClient(server_url="http://localhost:8000", token="test-token")
    assert client.server_url == "http://localhost:8000"


def test_client_build_headers():
    client = FluxClient(server_url="http://localhost:8000", token="test-token")
    headers = client._build_headers()
    assert headers["Authorization"] == "Bearer test-token"
    assert headers["Content-Type"] == "application/json"


def test_client_no_token():
    client = FluxClient(server_url="http://localhost:8000")
    headers = client._build_headers()
    assert "Authorization" not in headers


def test_start_agent_url():
    client = FluxClient(server_url="http://localhost:8000")
    url = client._start_url("agents", "agent_chat")
    assert url == "http://localhost:8000/workflows/agents/agent_chat/run/stream"


def test_resume_url():
    client = FluxClient(server_url="http://localhost:8000")
    url = client._resume_url("agents", "agent_chat", "exec_123")
    assert url == "http://localhost:8000/workflows/agents/agent_chat/resume/exec_123/stream"


def _patched_async_client(transport: httpx.MockTransport):
    """Return a context manager that patches ``httpx.AsyncClient`` to use a mock transport."""
    real_cls = httpx.AsyncClient

    def factory(**kwargs):
        kwargs.pop("transport", None)
        return real_cls(transport=transport, **kwargs)

    return patch("flux.agents.flux_client.httpx.AsyncClient", factory)


@pytest.mark.asyncio
async def test_start_agent_posts_body_unwrapped():
    """Regression: Flux server accepts the body directly as workflow input, not wrapped."""
    client = FluxClient(server_url="http://test", token="t")
    captured: dict = {}

    async def fake_handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content
        captured["url"] = str(request.url)
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            content=b'data: {"execution_id":"exec-1"}\n\n',
        )

    transport = httpx.MockTransport(fake_handler)
    with _patched_async_client(transport):
        events = [e async for e in client.start_agent("coder")]

    body = json.loads(captured["body"])
    assert body == {"agent": "coder"}, f"Body should be unwrapped dict, got {body!r}"
    assert captured["url"] == "http://test/workflows/agents/agent_chat/run/stream"
    assert len(events) == 1
    assert events[0][0] == "exec-1"


@pytest.mark.asyncio
async def test_start_agent_buffers_multiline_sse_frames():
    """Regression: a single SSE frame can span multiple ``data:`` lines (pretty-printed JSON)."""
    client = FluxClient(server_url="http://test", token="t")

    multi_line_sse = (
        b"data: {\n"
        b'data:     "execution_id": "exec-42",\n'
        b'data:     "type": "task.progress",\n'
        b'data:     "value": {"token": "hi"}\n'
        b"data: }\n"
        b"\n"
    )

    async def fake_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            content=multi_line_sse,
        )

    transport = httpx.MockTransport(fake_handler)
    with _patched_async_client(transport):
        events = [e async for e in client.start_agent("coder")]

    assert len(events) == 1
    execution_id, payload = events[0]
    assert execution_id == "exec-42"
    assert payload["type"] == "task.progress"
    assert payload["value"]["token"] == "hi"


@pytest.mark.asyncio
async def test_resume_posts_body_unwrapped_with_message():
    """Regression: resume should post the message dict directly, not wrapped."""
    client = FluxClient(server_url="http://test", token="t")
    captured: dict = {}

    async def fake_handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content
        captured["url"] = str(request.url)
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            content=b'data: {"type":"task.progress","value":{"token":"ok"}}\n\n',
        )

    transport = httpx.MockTransport(fake_handler)
    with _patched_async_client(transport):
        events = [e async for e in client.resume("exec-1", message="hi")]

    body = json.loads(captured["body"])
    assert body == {"message": "hi"}
    assert captured["url"] == "http://test/workflows/agents/agent_chat/resume/exec-1/stream"
    assert len(events) == 1
    assert events[0]["type"] == "task.progress"
    assert events[0]["value"]["token"] == "ok"
