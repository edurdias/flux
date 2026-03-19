"""
Per-Tool Option Overrides.

Demonstrates overriding Flux task options on individual MCP tools using
with_options(). The global timeout is 10s, but execute_workflow_sync gets
a 120s timeout since it waits for completion.

Prerequisites:
    1. Start Flux server: flux start server
    2. Start Flux worker: flux start worker worker-1
    3. Start Flux MCP server: flux start mcp

Usage:
    flux workflow run mcp_tool_override
"""

from __future__ import annotations

from flux import ExecutionContext, workflow
from flux.tasks.mcp import mcp


@workflow
async def mcp_tool_override(ctx: ExecutionContext):
    async with mcp("http://localhost:8080/mcp", name="flux", timeout=10) as client:
        tools = await client.discover()

        list_wf = await tools.list_workflows()

        execute = tools.execute_workflow_sync.with_options(timeout=120)
        result = await execute(
            workflow_name="hello_world",
            input_data={"name": "Flux"},
        )

        return {"workflows": list_wf, "execution": result}
