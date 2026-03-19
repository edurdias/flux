"""
MCP Tool Calls with Retry and Timeout.

Calls MCP tools with Flux task options applied globally. All discovered tools
inherit retry (3 attempts) and timeout (30s) from the mcp() client.

Prerequisites:
    1. Start Flux server: flux start server
    2. Start Flux worker: flux start worker worker-1
    3. Start Flux MCP server: flux start mcp

Usage:
    flux workflow run mcp_tool_call '{"workflow_name": "hello_world"}'
"""

from __future__ import annotations

from flux import ExecutionContext, workflow
from flux.tasks.mcp import mcp


@workflow.with_options(name="mcp_tool_call")
async def tool_call(ctx: ExecutionContext):
    workflow_name = ctx.input.get("workflow_name", "hello_world")

    async with mcp(
        "http://localhost:8080/mcp",
        name="flux",
        retry_max_attempts=3,
        timeout=30,
    ) as client:
        tools = await client.discover()

        details = await tools.get_workflow_details(workflow_name=workflow_name)

        executions = await tools.list_workflow_executions(
            workflow_name=workflow_name,
            limit=5,
        )

        return {
            "workflow": details,
            "recent_executions": executions,
        }
