"""
AI Agent with MCP Tools.

Combines the mcp() and agent() primitives — MCP tools are discovered at
runtime and passed directly to the LLM agent, which autonomously decides
which tools to call.

Prerequisites:
    1. Install Ollama: https://ollama.ai
    2. Pull a model: ollama pull llama3.2
    3. Start Ollama: ollama serve
    4. Start Flux server: flux start server
    5. Start Flux worker: flux start worker worker-1
    6. Start Flux MCP server: flux start mcp

Usage:
    flux workflow run mcp_agent_assistant '{"question": "What workflows are available?"}'
    flux workflow run mcp_agent_assistant '{"question": "Run hello_world with name Alice"}'
"""

from __future__ import annotations

from flux import ExecutionContext, workflow
from flux.tasks.ai import agent
from flux.tasks.mcp import mcp


@workflow.with_options(name="mcp_agent_assistant")
async def agent_assistant(ctx: ExecutionContext):
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
