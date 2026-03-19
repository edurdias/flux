from __future__ import annotations

import asyncio
from typing import Any, Literal
from urllib.parse import urlparse

from flux.task import task
from flux.tasks.mcp.auth import BearerAuthConfig, OAuthConfig
from flux.tasks.mcp.discovery import ToolSet, ToolSetOutputStorage
from flux.tasks.mcp.tool_builder import build_tools
from flux.utils import maybe_awaitable


class MCPClient:
    def __init__(
        self,
        server: Any,
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
    ):
        self._server = server
        self._auth = auth
        self._name = name or self._derive_name(server)
        self._connection = connection
        self._connect_timeout = connect_timeout
        _DEFAULTS = {
            "retry_max_attempts": 0,
            "retry_delay": 1,
            "retry_backoff": 2,
            "timeout": 0,
            "cache": False,
        }
        self._task_options = {
            k: v
            for k, v in {
                "retry_max_attempts": retry_max_attempts,
                "retry_delay": retry_delay,
                "retry_backoff": retry_backoff,
                "timeout": timeout,
                "cache": cache,
            }.items()
            if (k, v) not in _DEFAULTS.items()
        }
        self._fastmcp_client: Any | None = None
        self._lock = asyncio.Lock()
        self._discover_task: task | None = None
        self._rediscover_count: int = 0

    @staticmethod
    def _derive_name(server: Any) -> str:
        if isinstance(server, str):
            try:
                parsed = urlparse(server)
                if parsed.hostname:
                    return parsed.hostname
            except Exception:
                pass
        return "default"

    async def _resolve_auth(self) -> Any:
        if self._auth is None:
            return None

        if isinstance(self._auth, BearerAuthConfig):
            token = None
            if self._auth.provider:
                token = await maybe_awaitable(self._auth.provider())
            elif self._auth.secret:
                from flux.secret_managers import SecretManager

                secrets = SecretManager.current().get([self._auth.secret])
                token = secrets.get(self._auth.secret)
            else:
                token = self._auth.token

            from fastmcp.client.auth import BearerAuth

            return BearerAuth(token=token)

        if isinstance(self._auth, OAuthConfig):
            from fastmcp.client.auth import OAuth

            kwargs: dict[str, Any] = {}
            if self._auth.scopes is not None:
                kwargs["scopes"] = self._auth.scopes
            if self._auth.client_id is not None:
                kwargs["client_id"] = self._auth.client_id
            if self._auth.client_secret is not None:
                kwargs["client_secret"] = self._auth.client_secret
            if self._auth.client_name is not None:
                kwargs["client_name"] = self._auth.client_name
            if self._auth.token_storage is not None:
                kwargs["token_storage"] = self._auth.token_storage
            if self._auth.callback_port is not None:
                kwargs["callback_port"] = self._auth.callback_port
            return OAuth(**kwargs)

        return None

    async def _get_connection(self) -> Any:
        async with self._lock:
            if self._fastmcp_client is not None:
                return self._fastmcp_client

            from fastmcp import Client

            auth = await self._resolve_auth()
            client = Client(
                self._server,
                auth=auth,
                init_timeout=self._connect_timeout,
            )
            await client.__aenter__()

            if self._connection == "session":
                self._fastmcp_client = client

            return client

    async def _close_connection(self) -> None:
        async with self._lock:
            if self._fastmcp_client is not None:
                try:
                    await self._fastmcp_client.__aexit__(None, None, None)
                except Exception:
                    pass
                self._fastmcp_client = None

    def _discard_connection(self) -> None:
        self._fastmcp_client = None

    async def __aenter__(self) -> MCPClient:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self._close_connection()

    async def _discover_impl(self) -> ToolSet:
        connection = await self._get_connection()
        tools_result = await connection.list_tools()
        schemas = [
            {
                "name": tool.name,
                "description": tool.description or "",
                "inputSchema": tool.inputSchema if hasattr(tool, "inputSchema") else {},
            }
            for tool in tools_result
        ]
        tools_dict = build_tools(schemas, self, self._name, **self._task_options)
        if self._connection == "per-call":
            await self._close_connection()
        return ToolSet(tools_dict, schemas)

    async def discover(self) -> ToolSet:
        if self._discover_task is None:
            output_storage = ToolSetOutputStorage(self, self._name, **self._task_options)

            @task.with_options(
                name=f"mcp_{self._name}_discover",
                output_storage=output_storage,
            )
            async def discover_tools() -> ToolSet:
                return await self._discover_impl()

            self._discover_task = discover_tools

        return await self._discover_task()

    async def rediscover(self) -> ToolSet:
        self._rediscover_count += 1
        count = self._rediscover_count
        output_storage = ToolSetOutputStorage(self, self._name, **self._task_options)

        @task.with_options(
            name=f"mcp_{self._name}_rediscover_{count}",
            output_storage=output_storage,
        )
        async def rediscover_tools() -> ToolSet:
            return await self._discover_impl()

        return await rediscover_tools()
