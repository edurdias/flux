from __future__ import annotations

from typing import Literal

from flux.tasks.mcp.auth import bearer, oauth, BearerAuthConfig, OAuthConfig
from flux.tasks.mcp.client import MCPClient
from flux.tasks.mcp.discovery import ToolSet
from flux.tasks.mcp.errors import ToolExecutionError


def mcp(
    server: str | object,
    *,
    auth: BearerAuthConfig | OAuthConfig | None = None,
    name: str | None = None,
    connection: Literal["session", "per-call"] = "session",
    connect_timeout: int = 10,
    retry_max_attempts: int = 0,
    retry_delay: int = 1,
    retry_backoff: int = 2,
    timeout: int = 0,
    cache: bool = False,
) -> MCPClient:
    return MCPClient(
        server=server,
        auth=auth,
        name=name,
        connection=connection,
        connect_timeout=connect_timeout,
        retry_max_attempts=retry_max_attempts,
        retry_delay=retry_delay,
        retry_backoff=retry_backoff,
        timeout=timeout,
        cache=cache,
    )


__all__ = [
    "mcp",
    "bearer",
    "oauth",
    "BearerAuthConfig",
    "OAuthConfig",
    "MCPClient",
    "ToolSet",
    "ToolExecutionError",
]
