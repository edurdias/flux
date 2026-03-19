"""
Authenticated MCP Server Connection.

Connects to an MCP server using bearer token authentication via Flux's
secret store. The token is resolved at connection time (not at mcp()
creation time), so it stays fresh across pause/resume cycles.

Prerequisites:
    1. Store a secret: flux secret set MCP_API_KEY <your-token>
    2. Start Flux server: flux start server
    3. Start Flux worker: flux start worker worker-1

Usage:
    flux workflow run mcp_authenticated
"""

from __future__ import annotations

from flux import ExecutionContext, workflow
from flux.tasks.mcp import bearer, mcp


@workflow.with_options(name="mcp_authenticated")
async def authenticated(ctx: ExecutionContext):
    async with mcp(
        "https://api.example.com/mcp",
        name="external",
        auth=bearer(secret="MCP_API_KEY"),
        retry_max_attempts=3,
    ) as client:
        tools = await client.discover()
        return [t.name for t in tools]
