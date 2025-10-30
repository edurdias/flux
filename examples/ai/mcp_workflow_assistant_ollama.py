"""
MCP-Integrated Workflow Assistant using Ollama.

This example demonstrates proper Model Context Protocol (MCP) integration with an AI agent.
The assistant dynamically discovers and uses Flux workflow management tools through the MCP
protocol, showcasing a standards-compliant approach to connecting AI frameworks with MCP servers.

Key Features:
- **Dynamic Tool Discovery**: Uses MCP protocol to discover tools at runtime
- **Standards-Compliant**: No hardcoded tool definitions, fully MCP-native
- **Workflow Suggestions**: Intelligently recommends workflows based on user intent
- **Execution Monitoring**: Proactively tracks workflow execution status
- **Multi-turn Conversations**: Maintains context across interactions with pause/resume
- **Production Pattern**: Real-world example of MCP + AI framework integration

Prerequisites:
    1. Install Ollama: https://ollama.ai
    2. Pull a model that supports tools: ollama pull llama3.2
    3. Start Ollama service: ollama serve
    4. Start Flux server: poetry run flux start server
    5. Start Flux worker: poetry run flux start worker worker-1
    6. Start Flux MCP server: poetry run flux start mcp

Usage:
    # Start a conversation about workflows
    flux workflow run mcp_workflow_assistant_ollama '{"message": "What workflows are available?"}'

    # Continue the conversation
    flux workflow resume mcp_workflow_assistant_ollama <execution_id> '{"message": "Run the hello_world workflow"}'

    # Ask for workflow suggestions
    flux workflow run mcp_workflow_assistant_ollama '{"message": "I want to process some data"}'

    # Check execution status
    flux workflow run mcp_workflow_assistant_ollama '{"message": "Show me the status of execution abc-123"}'

Example Conversation:
    User: "What workflows are available?"
    Assistant: *calls list_workflows via MCP* → Lists all workflows with descriptions

    User: "Run the hello_world workflow with name 'Alice'"
    Assistant: *calls execute_workflow_async via MCP* → Returns execution ID and confirms start

    User: "What's the status?"
    Assistant: *calls get_execution_status via MCP* → Shows current status and details
"""

from __future__ import annotations

import json
from typing import Any

from fastmcp import Client
from ollama import AsyncClient

from flux import ExecutionContext, task, workflow
from flux.tasks import pause


# =============================================================================
# MCP Integration - Dynamic Tool Discovery
# =============================================================================


@task.with_options(retry_max_attempts=3, retry_delay=1, retry_backoff=2, timeout=30)
async def discover_mcp_tools(mcp_url: str) -> list[dict[str, Any]]:
    """
    Connect to MCP server and discover available tools.

    Args:
        mcp_url: URL of the MCP server (e.g., "http://localhost:8080/mcp")

    Returns:
        List of MCP tool schemas
    """
    try:
        async with Client(mcp_url) as client:
            tools = await client.list_tools()

            tool_schemas = []
            for tool in tools:
                tool_schemas.append(
                    {
                        "name": tool.name,
                        "description": tool.description,
                        "input_schema": tool.inputSchema,
                    },
                )

            return tool_schemas

    except Exception as e:
        raise RuntimeError(
            f"Failed to connect to MCP server at {mcp_url}: {str(e)}. "
            "Make sure the Flux MCP server is running: poetry run flux start mcp",
        ) from e


@task
async def convert_mcp_tools_to_ollama(mcp_tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Convert MCP tool schemas to Ollama function calling format.

    Args:
        mcp_tools: List of MCP tool schemas

    Returns:
        List of tools in Ollama format
    """
    ollama_tools = []

    for tool in mcp_tools:
        ollama_tool = {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool["description"],
                "parameters": tool["input_schema"],
            },
        }
        ollama_tools.append(ollama_tool)

    return ollama_tools


@task.with_options(retry_max_attempts=3, retry_delay=1, retry_backoff=2, timeout=60)
async def execute_mcp_tool(
    mcp_url: str,
    tool_name: str,
    tool_args: dict[str, Any],
) -> str:
    """
    Execute a tool via MCP protocol.

    Args:
        mcp_url: URL of the MCP server
        tool_name: Name of the tool to execute
        tool_args: Arguments for the tool

    Returns:
        JSON string with tool execution result
    """
    try:
        async with Client(mcp_url) as client:
            result = await client.call_tool(tool_name, tool_args)

            if result and len(result) > 0:
                content = result[0]
                if hasattr(content, "text"):
                    return content.text
                elif isinstance(content, dict) and "text" in content:
                    return content["text"]
                else:
                    return json.dumps({"result": str(content)})
            else:
                return json.dumps({"result": "Tool executed successfully but returned no content"})

    except Exception as e:
        error_message = f"Failed to execute tool '{tool_name}': {str(e)}"
        return json.dumps({"error": error_message, "success": False})


# =============================================================================
# LLM Integration with MCP Tools
# =============================================================================


@task.with_options(retry_max_attempts=3, retry_delay=1, retry_backoff=2, timeout=60)
async def call_ollama_with_mcp_tools(
    messages: list[dict[str, Any]],
    system_prompt: str,
    model: str,
    ollama_url: str,
    ollama_tools: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Call Ollama API with dynamically discovered MCP tools.

    Args:
        messages: Conversation history
        system_prompt: System prompt
        model: Ollama model name (must support tools, e.g., llama3.2, mistral)
        ollama_url: Ollama server URL
        ollama_tools: List of tools in Ollama format

    Returns:
        Response dict with 'message' and optional 'tool_calls'
    """
    try:
        client = AsyncClient(host=ollama_url)

        full_messages = [{"role": "system", "content": system_prompt}]
        full_messages.extend(messages)

        response = await client.chat(
            model=model,
            messages=full_messages,
            tools=ollama_tools,
        )

        return response

    except Exception as e:
        raise RuntimeError(
            f"Failed to call Ollama API: {str(e)}. "
            "Make sure Ollama is running (ollama serve) and the model supports tools (e.g., llama3.2, mistral).",
        ) from e


# =============================================================================
# Main Workflow
# =============================================================================


@workflow.with_options(name="mcp_workflow_assistant_ollama")
async def mcp_workflow_assistant_ollama(ctx: ExecutionContext[dict[str, Any]]):
    """
    AI assistant that manages Flux workflows via MCP protocol.

    Discovers tools dynamically from the MCP server and routes all
    tool calls through the MCP protocol.

    Initial Input format:
    {
        "message": "User's message",
        "system_prompt": "Optional custom system prompt",
        "model": "llama3.2",                          # or "mistral" (must support tools)
        "ollama_url": "http://localhost:11434",       # Ollama server URL
        "mcp_url": "http://localhost:8080/mcp",       # Flux MCP server URL
        "max_turns": 20                               # Maximum conversation turns
    }

    Resume Input format:
    {
        "message": "User's next message"
    }
    """
    # Get initial configuration
    initial_input = ctx.input or {}
    system_prompt = initial_input.get(
        "system_prompt",
        """You are a helpful AI assistant for managing Flux workflows via MCP (Model Context Protocol).

Your role:
- Help users discover, run, and manage workflows
- Suggest relevant workflows based on user intent
- Monitor workflow executions and provide status updates
- Explain workflow capabilities and requirements

When a user asks what they can do or what workflows exist:
1. Call list_workflows to discover available workflows
2. Analyze the workflows and their descriptions
3. Suggest the most relevant ones based on user needs

When executing workflows:
1. Use execute_workflow_async for asynchronous execution
2. Remember the execution_id for status tracking
3. Offer to monitor the execution

When monitoring executions:
1. Use get_execution_status with detailed=true for comprehensive info
2. Explain the status in user-friendly terms
3. Suggest next steps based on the status

Be proactive, helpful, and explain technical details in accessible language.""",
    )
    model = initial_input.get("model", "llama3.2")
    ollama_url = initial_input.get("ollama_url", "http://localhost:11434")
    mcp_url = initial_input.get("mcp_url", "http://localhost:8080/mcp")
    max_turns = initial_input.get("max_turns", 20)

    mcp_tools = await discover_mcp_tools(mcp_url)
    ollama_tools = await convert_mcp_tools_to_ollama(mcp_tools)

    messages: list[dict[str, Any]] = []
    execution_ids: list[str] = []  # Track execution IDs across conversation

    first_message = initial_input.get("message", "")
    if not first_message:
        return {
            "error": "No message provided in initial input",
            "execution_id": ctx.execution_id,
            "available_tools": [t["name"] for t in mcp_tools],
        }

    messages.append({"role": "user", "content": first_message})

    for turn in range(max_turns):
        response = await call_ollama_with_mcp_tools(
            messages=messages,
            system_prompt=system_prompt,
            model=model,
            ollama_url=ollama_url,
            ollama_tools=ollama_tools,
        )

        message = response["message"]

        if message.get("tool_calls"):
            for tool_call in message["tool_calls"]:
                tool_name = tool_call["function"]["name"]
                tool_args = tool_call["function"]["arguments"]

                tool_result = await execute_mcp_tool(mcp_url, tool_name, tool_args)

                # Track execution IDs for workflow executions
                if "execute_workflow" in tool_name and "execution_id" in tool_result:
                    try:
                        result_data = json.loads(tool_result)
                        if "execution_id" in result_data:
                            execution_ids.append(result_data["execution_id"])
                    except json.JSONDecodeError:
                        pass

                messages.append(
                    {
                        "role": "tool",
                        "content": tool_result,
                    },
                )

            response = await call_ollama_with_mcp_tools(
                messages=messages,
                system_prompt=system_prompt,
                model=model,
                ollama_url=ollama_url,
                ollama_tools=ollama_tools,
            )

            message = response["message"]

        assistant_content = message.get("content", "")
        messages.append({"role": "assistant", "content": assistant_content})

        resume_input = await pause(f"waiting_for_user_input_turn_{turn + 1}")

        next_message = resume_input.get("message", "") if resume_input else ""
        if not next_message:
            return {
                "conversation_history": messages,
                "total_turns": turn + 1,
                "tracked_executions": execution_ids,
                "execution_id": ctx.execution_id,
                "mcp_tools_used": [t["name"] for t in mcp_tools],
            }

        messages.append({"role": "user", "content": next_message})

    return {
        "conversation_history": messages,
        "total_turns": max_turns,
        "tracked_executions": execution_ids,
        "message": "Maximum conversation turns reached",
        "execution_id": ctx.execution_id,
        "mcp_tools_used": [t["name"] for t in mcp_tools],
    }


if __name__ == "__main__":  # pragma: no cover
    # Quick test of the workflow
    print("=" * 80)
    print("MCP Workflow Assistant Demo - Flux + Ollama + MCP")
    print("=" * 80 + "\n")

    try:
        # Test 1: Discover available workflows
        print("Test 1: Ask about available workflows\n")
        result = mcp_workflow_assistant_ollama.run(
            {
                "message": "What workflows are available?",
                "model": "llama3.2",
            },
        )

        if result.has_failed:
            raise Exception(f"Test 1 failed: {result.output}")

        conversation = result.output.get("conversation_history", [])
        if conversation:
            print(f"Assistant: {conversation[-1].get('content', 'No response')}\n")
        print("=" * 80 + "\n")

        # Test 2: Resume and ask about workflow details
        print("Test 2: Resume and ask for more details\n")
        result = mcp_workflow_assistant_ollama.resume(
            result.execution_id,
            {"message": "Tell me more about the hello_world workflow"},
        )

        if result.has_failed:
            raise Exception(f"Test 2 failed: {result.output}")

        conversation = result.output.get("conversation_history", [])
        if conversation:
            print(f"Assistant: {conversation[-1].get('content', 'No response')}\n")

        print("=" * 80)
        print("✓ MCP Workflow Assistant working successfully!")
        print("✓ Tools dynamically discovered via MCP protocol!")
        print("✓ Standards-compliant integration demonstrated!")
        print("=" * 80)

        # Show tools that were discovered
        mcp_tools = result.output.get("mcp_tools_used", [])
        if mcp_tools:
            print(f"\nMCP Tools Discovered: {', '.join(mcp_tools)}")

    except Exception as e:
        print(f"Error: {e}")
        print("\nMake sure all services are running:")
        print("1. Flux server: poetry run flux start server")
        print("2. Flux worker: poetry run flux start worker worker-1")
        print("3. Flux MCP server: poetry run flux start mcp")
        print("4. Ollama: ollama serve")
        print("5. Ollama model: ollama pull llama3.2")
