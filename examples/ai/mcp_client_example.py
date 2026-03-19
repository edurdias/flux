"""
MCP Client Primitive Example.

This example demonstrates the mcp() task primitive — a Flux-native way to
consume tools from any MCP server. Each discovered MCP tool becomes a Flux
@task with full support for retry, timeout, caching, event tracking, and
pause/resume.

Compare with:
- examples/ai/mcp_workflow_assistant_ollama.py (manual MCP integration, ~400 lines)

The mcp() primitive replaces manual tool discovery, connection management, and
tool execution with a clean context manager API. Tools are discovered at
runtime and can be passed directly to agent().

Prerequisites:
    1. Start Flux server: flux start server
    2. Start Flux worker: flux start worker worker-1
    3. Start Flux MCP server: flux start mcp

Usage:
    # Discover and list all available MCP tools
    flux workflow run mcp_tool_discovery

    # Call a specific MCP tool
    flux workflow run mcp_tool_call '{"workflow_name": "hello_world"}'

    # Use MCP tools with an AI agent (requires Ollama)
    flux workflow run mcp_agent_assistant '{"question": "What workflows are available?"}'
"""

from __future__ import annotations

from flux import ExecutionContext, workflow
from flux.tasks import pause
from flux.tasks.mcp import mcp, bearer


# ---------------------------------------------------------------------------
# Example 1: Basic tool discovery and execution
# ---------------------------------------------------------------------------


@workflow.with_options(name="mcp_tool_discovery")
async def tool_discovery(ctx: ExecutionContext):
    """Discover and list all tools from the Flux MCP server."""
    async with mcp("http://localhost:8080/mcp", name="flux") as client:
        tools = await client.discover()
        return {
            "tool_count": len(tools),
            "tools": [t.name for t in tools],
        }


# ---------------------------------------------------------------------------
# Example 2: Call MCP tools with retry and timeout
# ---------------------------------------------------------------------------


@workflow.with_options(name="mcp_tool_call")
async def tool_call(ctx: ExecutionContext):
    """Call MCP tools with Flux task options (retry, timeout)."""
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


# ---------------------------------------------------------------------------
# Example 3: Per-tool option overrides
# ---------------------------------------------------------------------------


@workflow.with_options(name="mcp_tool_override")
async def tool_override(ctx: ExecutionContext):
    """Override task options on individual MCP tools."""
    async with mcp("http://localhost:8080/mcp", name="flux", timeout=10) as client:
        tools = await client.discover()

        list_wf = await tools.list_workflows()

        execute = tools.execute_workflow_sync.with_options(timeout=120)
        result = await execute(
            workflow_name="hello_world",
            input_data={"name": "Flux"},
        )

        return {"workflows": list_wf, "execution": result}


# ---------------------------------------------------------------------------
# Example 4: Multi-turn assistant with MCP tools and pause/resume
# ---------------------------------------------------------------------------


@workflow.with_options(name="mcp_interactive")
async def interactive(ctx: ExecutionContext):
    """Interactive workflow that uses MCP tools across pause/resume cycles."""
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


# ---------------------------------------------------------------------------
# Example 5: MCP tools + agent() integration
# ---------------------------------------------------------------------------


@workflow.with_options(name="mcp_agent_assistant")
async def agent_assistant(ctx: ExecutionContext):
    """Use MCP tools as agent tools — the LLM decides which tools to call."""
    from flux.tasks.ai import agent

    question = ctx.input.get("question", "What workflows are available?")

    async with mcp("http://localhost:8080/mcp", name="flux") as client:
        tools = await client.discover()

        assistant = agent(
            system_prompt=(
                "You are a Flux workflow management assistant. "
                "Use the available tools to help users manage their workflows. "
                "Be concise and helpful."
            ),
            model="ollama/llama3.2",
            tools=list(tools),
            max_tool_calls=5,
        )

        response = await assistant(question)
        return {"question": question, "response": response}


# ---------------------------------------------------------------------------
# Example 6: Bearer token authentication
# ---------------------------------------------------------------------------


@workflow.with_options(name="mcp_authenticated")
async def authenticated(ctx: ExecutionContext):
    """Connect to an MCP server with bearer token auth."""
    async with mcp(
        "https://api.example.com/mcp",
        name="external",
        auth=bearer(secret="MCP_API_KEY"),
        retry_max_attempts=3,
    ) as client:
        tools = await client.discover()
        return [t.name for t in tools]
