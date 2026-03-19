from __future__ import annotations

import pytest

from flux.tasks.mcp.auth import BearerAuthConfig, OAuthConfig, bearer, oauth
from flux.tasks.mcp.client import MCPClient


def test_mcp_client_stores_config():
    client = MCPClient(
        server="https://example.com/mcp",
        name="test",
        connection="session",
    )
    assert client._server == "https://example.com/mcp"
    assert client._name == "test"
    assert client._connection == "session"


def test_mcp_client_defaults():
    client = MCPClient(server="https://example.com/mcp")
    assert client._name == "example.com"
    assert client._connection == "session"
    assert client._connect_timeout == 10
    assert client._auth is None


def test_mcp_client_name_from_hostname():
    client = MCPClient(server="https://weather.example.com/mcp")
    assert client._name == "weather.example.com"


def test_mcp_client_name_explicit_overrides_hostname():
    client = MCPClient(server="https://weather.example.com/mcp", name="weather")
    assert client._name == "weather"


def test_mcp_client_name_from_fastmcp_instance():
    client = MCPClient(server=object())
    assert client._name == "default"


@pytest.mark.asyncio
async def test_mcp_client_aenter_returns_self():
    client = MCPClient(server="https://example.com/mcp")
    result = await client.__aenter__()
    assert result is client
    await client.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_mcp_client_aenter_does_not_connect():
    client = MCPClient(server="https://example.com/mcp")
    async with client:
        assert client._fastmcp_client is None


@pytest.mark.asyncio
async def test_mcp_client_aexit_noop_without_connection():
    client = MCPClient(server="https://example.com/mcp")
    async with client:
        pass
    assert client._fastmcp_client is None


def test_mcp_client_with_bearer_auth():
    client = MCPClient(
        server="https://example.com/mcp",
        auth=bearer("my-token"),
    )
    assert isinstance(client._auth, BearerAuthConfig)
    assert client._auth.token == "my-token"


def test_mcp_client_with_oauth():
    client = MCPClient(
        server="https://example.com/mcp",
        auth=oauth(scopes=["read"]),
    )
    assert isinstance(client._auth, OAuthConfig)


def test_mcp_client_task_options():
    client = MCPClient(
        server="https://example.com/mcp",
        retry_max_attempts=3,
        timeout=30,
    )
    assert client._task_options["retry_max_attempts"] == 3
    assert client._task_options["timeout"] == 30


def test_mcp_client_task_options_excludes_defaults():
    client = MCPClient(server="https://example.com/mcp")
    assert "retry_max_attempts" not in client._task_options
    assert "retry_delay" not in client._task_options
    assert "timeout" not in client._task_options
    assert "cache" not in client._task_options
