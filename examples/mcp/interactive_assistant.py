"""
Interactive MCP Assistant with Pause/Resume.

Uses MCP tools across pause/resume cycles. The workflow lists available
workflows, pauses for user input, then executes the chosen workflow.
Demonstrates that MCP connections reconnect lazily after resume.

Prerequisites:
    1. Start Flux server: flux start server
    2. Start Flux worker: flux start worker worker-1
    3. Start Flux MCP server: flux start mcp

Usage:
    flux workflow run mcp_interactive
    flux workflow resume mcp_interactive <execution_id> '{"workflow_name": "hello_world"}'
"""

from __future__ import annotations

from flux import ExecutionContext, workflow
from flux.tasks import pause
from flux.tasks.mcp import mcp


@workflow.with_options(name="mcp_interactive")
async def interactive(ctx: ExecutionContext):
    async with mcp("http://localhost:8080/mcp", name="flux") as client:
        tools = await client.discover()

        available = await tools.list_workflows()
        user_input = await pause(
            "choose_workflow",
            output={"message": "Which workflow to run?", "available": available},
        )

        workflow_name = user_input.get("workflow_name", "hello_world")
        result = await tools.execute_workflow_sync(
            workflow_name=workflow_name,
            input_data=user_input.get("input_data", {}),
        )

        return {"workflow": workflow_name, "result": result}
