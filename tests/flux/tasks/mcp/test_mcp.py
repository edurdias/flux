from __future__ import annotations

from flux.tasks.mcp import mcp, bearer
from flux.tasks.mcp.client import MCPClient


def test_mcp_returns_mcp_client():
    client = mcp("https://example.com/mcp")
    assert isinstance(client, MCPClient)


def test_mcp_with_auth():
    client = mcp("https://example.com/mcp", auth=bearer("token"))
    assert client._auth.token == "token"


def test_mcp_with_name():
    client = mcp("https://example.com/mcp", name="weather")
    assert client._name == "weather"


def test_mcp_with_task_options():
    client = mcp(
        "https://example.com/mcp",
        retry_max_attempts=3,
        timeout=30,
    )
    assert client._task_options["retry_max_attempts"] == 3
    assert client._task_options["timeout"] == 30


def test_mcp_with_per_call_connection():
    client = mcp("https://example.com/mcp", connection="per-call")
    assert client._connection == "per-call"
