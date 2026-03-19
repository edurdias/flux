"""
MCP Tool Discovery.

Discovers and lists all available tools from an MCP server using the mcp()
primitive. Each tool is automatically exposed as a Flux @task.

Prerequisites:
    1. Start Flux server: flux start server
    2. Start Flux worker: flux start worker worker-1
    3. Start Flux MCP server: flux start mcp

Usage:
    flux workflow run mcp_tool_discovery
"""

from __future__ import annotations

from flux import ExecutionContext, workflow
from flux.tasks.mcp import mcp


@workflow.with_options(name="mcp_tool_discovery")
async def tool_discovery(ctx: ExecutionContext):
    async with mcp("http://localhost:8080/mcp", name="flux") as client:
        tools = await client.discover()
        return {
            "tool_count": len(tools),
            "tools": [t.name for t in tools],
        }
