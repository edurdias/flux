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


@workflow
async def mcp_agent_assistant(ctx: ExecutionContext):
    question = ctx.input.get("question", "What workflows are available?")

    async with mcp("http://localhost:8080/mcp", name="flux") as client:
        tools = await client.discover()

        tool_subset = [
            tools.list_workflows,
            tools.get_workflow_details,
            tools.execute_workflow_async,
            tools.get_execution_status,
            tools.health_check,
        ]

        assistant = agent(
            system_prompt=(
                "You are a Flux workflow management assistant. "
                "Use the available tools to help users manage their workflows. "
                "Be concise and helpful."
            ),
            model="ollama/llama3.2",
            tools=tool_subset,
            max_tool_calls=3,
        )

        response = await assistant(question)
        return {"question": question, "response": response}


if __name__ == "__main__":  # pragma: no cover
    questions = [
        "What workflows are available?",
        "Run the hello_world workflow",
    ]

    for question in questions:
        try:
            print("=" * 80)
            print(f"Q: {question}")
            print("=" * 80)

            result = mcp_agent_assistant.run({"question": question})

            if result.has_failed:
                raise Exception(f"Workflow failed: {result.output}")

            print(f"\nA: {result.output['response']}\n")

        except Exception as e:
            print(f"Error: {e}")
            print("Make sure Ollama is running: ollama serve")
            print("And model is pulled: ollama pull llama3.2")
            print("And Flux MCP server is running: flux start mcp\n")
