"""
Multi-Server MCP Workflow.

Connects to multiple MCP servers simultaneously, each with its own
connection lifecycle and tool namespace. The workflow orchestrates
tools from both servers in a single execution.

Prerequisites:
    1. Start Flux server: flux start server
    2. Start Flux worker: flux start worker worker-1
    3. Start Flux MCP server: flux start mcp
    4. Start a second MCP server on a different port

Usage:
    flux workflow run mcp_multi_server
"""

from __future__ import annotations

from flux import ExecutionContext, workflow
from flux.tasks.mcp import mcp


@workflow
async def mcp_multi_server(ctx: ExecutionContext):
    async with mcp("http://localhost:8080/mcp", name="primary") as primary:
        async with mcp("http://localhost:8081/mcp", name="secondary") as secondary:
            primary_tools = await primary.discover()
            secondary_tools = await secondary.discover()

            primary_result = await primary_tools.list_workflows()
            secondary_result = await secondary_tools.health_check()

            return {
                "primary_tools": [t.name for t in primary_tools],
                "secondary_tools": [t.name for t in secondary_tools],
                "primary_result": primary_result,
                "secondary_result": secondary_result,
            }


if __name__ == "__main__":  # pragma: no cover
    import json

    print("Connecting to multiple MCP servers...")
    ctx = mcp_multi_server.run()
    if ctx.has_succeeded:
        print(f"\nResult:\n{json.dumps(ctx.output, indent=2)}")
    else:
        print(f"Failed: {ctx.output}")
