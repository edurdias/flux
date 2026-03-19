"""
OAuth-Authenticated MCP Server Connection.

Connects to an MCP server using OAuth 2.1 with PKCE. FastMCP handles the
full OAuth flow: server discovery, dynamic client registration, authorization
via browser, token exchange, and automatic refresh.

Prerequisites:
    1. Start Flux server: flux start server
    2. Start Flux worker: flux start worker worker-1
    3. MCP server must support OAuth 2.1 (RFC 8414 discovery)

Usage:
    flux workflow run mcp_oauth
"""

from __future__ import annotations

from flux import ExecutionContext, workflow
from flux.tasks.mcp import mcp, oauth


@workflow
async def mcp_oauth(ctx: ExecutionContext):
    async with mcp(
        "https://api.example.com/mcp",
        name="external",
        auth=oauth(
            scopes=["read", "write"],
            client_name="Flux Workflow",
        ),
        retry_max_attempts=3,
    ) as client:
        tools = await client.discover()
        return [t.name for t in tools]


if __name__ == "__main__":  # pragma: no cover
    import json

    print("Connecting to OAuth-protected MCP server...")
    ctx = mcp_oauth.run()
    print(f"\nDiscovered tools:\n{json.dumps(ctx.output, indent=2)}")
