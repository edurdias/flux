"""
Multi-Turn AI Assistant with MCP Tool Integration using Ollama.

This example demonstrates a multi-turn conversational AI assistant that can autonomously
use tools via the Model Context Protocol (MCP). The assistant decides when to use tools
versus when to respond directly to the user, creating a natural conversation flow.

Key Features:
- **Natural Tool Calling**: LLM autonomously decides when to use tools vs respond
- **Multi-Turn Conversations**: Maintains context across multiple interactions
- **Dynamic Tool Discovery**: Discovers available tools from MCP servers at runtime
- **Multi-MCP Support**: Can connect to multiple MCP servers simultaneously
- **Autonomous Reasoning**: Assistant decides its own approach to problems

Prerequisites:
    1. Install Ollama: https://ollama.ai
    2. Pull a model that supports tools: ollama pull llama3.2
    3. Start Ollama service: ollama serve
    4. Start Flux server: poetry run flux start server
    5. Start Flux worker: poetry run flux start worker worker-1
    6. Start Flux MCP server: poetry run flux start mcp

Usage:
    # Ask the assistant to help with workflows
    flux workflow run multi_turn_assistant_ollama '{"message": "What workflows are available?"}'

    # Continue the conversation
    flux workflow resume multi_turn_assistant_ollama <execution_id> '{"message": "Run the hello_world workflow"}'

    # Use multiple MCP servers (e.g., Flux + web search)
    flux workflow run multi_turn_assistant_ollama '{
        "message": "Search for best practices in distributed systems",
        "mcp_urls": ["http://localhost:8080/mcp", "http://localhost:8081/mcp"]
    }'

Example Conversation Flow:
    User: "List available workflows and run the hello_world one"

    Assistant: *calls list_workflows*
    Assistant: *sees results, calls execute_workflow_async*
    Assistant: *sees execution started*
    Assistant: "I found 5 workflows. I've started the hello_world workflow
               with execution ID abc-123. It should complete shortly."

    User: "What's the status?"
    Assistant: *calls get_execution_status*
    Assistant: "The workflow completed successfully! Output: Hello, World!"
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


@task
async def discover_tools_from_mcp_servers(
    mcp_urls: list[str],
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """
    Connect to multiple MCP servers and discover all available tools.

    Args:
        mcp_urls: List of MCP server URLs

    Returns:
        Tuple of (tool schemas, tool_to_server_mapping)
    """
    all_tools = []
    tool_to_server = {}
    failed_servers = []

    for mcp_url in mcp_urls:
        try:
            async with Client(mcp_url) as client:
                tools = await client.list_tools()

                for tool in tools:
                    tool_schema = {
                        "name": tool.name,
                        "description": tool.description,
                        "input_schema": tool.inputSchema,
                    }
                    all_tools.append(tool_schema)
                    tool_to_server[tool.name] = mcp_url

        except Exception as e:
            failed_servers.append(mcp_url)
            print(f"Warning: Could not connect to MCP server at {mcp_url}: {str(e)}")
            continue

    if not all_tools:
        error_msg = "Could not discover any tools from MCP servers. "
        if failed_servers:
            error_msg += f"\nFailed servers: {', '.join(failed_servers)}"
        error_msg += "\n\nMake sure at least one MCP server is running:"
        error_msg += "\n  poetry run flux start mcp"
        raise RuntimeError(error_msg)

    return all_tools, tool_to_server


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
        mcp_url: URL of the MCP server that hosts this tool
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
# LLM Integration
# =============================================================================


@task.with_options(retry_max_attempts=3, retry_delay=1, retry_backoff=2, timeout=90)
async def call_ollama_with_tools(
    messages: list[dict[str, Any]],
    system_prompt: str,
    model: str,
    ollama_url: str,
    ollama_tools: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Call Ollama API with tools for multi-turn reasoning.

    Args:
        messages: Conversation history
        system_prompt: System instructions for the assistant
        model: Ollama model name (must support tools, e.g., llama3.2)
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
            "Make sure Ollama is running (ollama serve) and the model supports tools (e.g., llama3.2).",
        ) from e


# =============================================================================
# Main Workflow
# =============================================================================


@workflow.with_options(name="multi_turn_assistant_ollama")
async def multi_turn_assistant_ollama(ctx: ExecutionContext[dict[str, Any]]):
    """
    Multi-turn AI assistant with autonomous tool use via MCP.

    The assistant uses a natural tool calling pattern where it autonomously
    decides when to use tools versus when to respond to the user. This creates
    a fluid conversation where the assistant can chain multiple tool calls
    together to complete complex tasks.

    Initial Input format:
    {
        "message": "User's request or question",
        "system_prompt": "Optional custom system prompt",
        "model": "llama3.2",                                    # or "mistral" (must support tools)
        "ollama_url": "http://localhost:11434",                 # Ollama server URL
        "mcp_urls": [                                          # List of MCP servers
            "http://localhost:8080/mcp",                        # Flux MCP server
            "http://localhost:8081/mcp"                         # Optional: additional MCP servers
        ],
        "max_turns": 20                                        # Maximum conversation turns
    }

    Resume Input format:
    {
        "message": "User's next message or question"
    }
    """
    # Get initial configuration
    initial_input = ctx.input or {}
    system_prompt = initial_input.get(
        "system_prompt",
        """You are a helpful AI assistant with access to tools via MCP.

Your approach:
- When users ask questions, think about what information you need
- Use tools when you need real-time data or to perform actions
- You can chain multiple tool calls together to complete complex tasks
- Explain your reasoning and what you're doing

Tool usage guidelines:
- Call tools when you need current information (workflows, executions, etc.)
- You can call multiple tools in sequence - keep using tools until you have enough information
- Once you have what you need, provide a clear response to the user

Be helpful, clear, and proactive in using tools to assist users.""",
    )
    model = initial_input.get("model", "llama3.2")
    ollama_url = initial_input.get("ollama_url", "http://localhost:11434")
    mcp_urls = initial_input.get(
        "mcp_urls",
        ["http://localhost:8080/mcp"],  # Default to Flux MCP server
    )
    max_turns = initial_input.get("max_turns", 20)

    # Discover tools from all MCP servers
    mcp_tools, tool_to_server = await discover_tools_from_mcp_servers(mcp_urls)
    ollama_tools = await convert_mcp_tools_to_ollama(mcp_tools)

    # Initialize conversation state
    messages: list[dict[str, Any]] = []
    tools_used: list[dict[str, Any]] = []

    # Process first message
    first_message = initial_input.get("message", "")
    if not first_message:
        return {
            "error": "No message provided in initial input",
            "execution_id": ctx.execution_id,
            "available_tools": [t["name"] for t in mcp_tools],
        }

    messages.append({"role": "user", "content": first_message})

    # Main conversation loop
    for turn in range(max_turns):
        # Call LLM - it decides whether to use tools or respond
        response = await call_ollama_with_tools(
            messages=messages,
            system_prompt=system_prompt,
            model=model,
            ollama_url=ollama_url,
            ollama_tools=ollama_tools,
        )

        message = response["message"]

        # Natural tool calling loop: keep using tools until LLM responds
        while message.get("tool_calls"):
            # Execute all requested tool calls
            for tool_call in message["tool_calls"]:
                tool_name = tool_call["function"]["name"]
                tool_args = tool_call["function"]["arguments"]

                # Find which MCP server hosts this tool
                mcp_url = tool_to_server.get(tool_name)
                if not mcp_url:
                    tool_result = json.dumps(
                        {"error": f"Tool '{tool_name}' not found in any MCP server"},
                    )
                else:
                    # Execute tool via appropriate MCP server
                    tool_result = await execute_mcp_tool(mcp_url, tool_name, tool_args)

                # Track tool usage
                tools_used.append(
                    {
                        "tool": tool_name,
                        "args": tool_args,
                        "turn": turn + 1,
                    },
                )

                # Add tool result to conversation
                messages.append(
                    {
                        "role": "tool",
                        "content": tool_result,
                    },
                )

            # Call LLM again with tool results - it decides what to do next
            response = await call_ollama_with_tools(
                messages=messages,
                system_prompt=system_prompt,
                model=model,
                ollama_url=ollama_url,
                ollama_tools=ollama_tools,
            )

            message = response["message"]

        # LLM returned content without tool calls - ready to respond to user
        assistant_content = message.get("content", "")
        if assistant_content:
            messages.append({"role": "assistant", "content": assistant_content})

        # Pause and wait for next user input, returning the assistant's response
        resume_input = await pause(
            f"waiting_for_user_input_turn_{turn + 1}",
            output=assistant_content,
        )

        # Get next message
        next_message = resume_input.get("message", "") if resume_input else ""
        if not next_message:
            return {
                "conversation_history": messages,
                "total_turns": turn + 1,
                "tools_used": tools_used,
                "execution_id": ctx.execution_id,
                "available_tools": [t["name"] for t in mcp_tools],
            }

        messages.append({"role": "user", "content": next_message})

    # Max turns reached
    return {
        "conversation_history": messages,
        "total_turns": max_turns,
        "tools_used": tools_used,
        "message": "Maximum conversation turns reached",
        "execution_id": ctx.execution_id,
        "available_tools": [t["name"] for t in mcp_tools],
    }


if __name__ == "__main__":  # pragma: no cover
    # Quick test of the workflow
    print("=" * 80)
    print("Multi-Turn Assistant Demo - Natural Tool Calling with MCP")
    print("=" * 80 + "\n")

    try:
        # Test: Ask assistant to discover and explain workflows
        print("Test: Ask assistant about available workflows\n")
        result = multi_turn_assistant_ollama.run(
            {
                "message": "What workflows are available? Pick one and tell me what it does.",
                "model": "llama3.2",
            },
        )

        if result.has_failed:
            raise Exception(f"Test failed: {result.output}")

        conversation = result.output.get("conversation_history", [])
        if conversation:
            print(f"Assistant: {conversation[-1].get('content', 'No response')}\n")

        tools_used = result.output.get("tools_used", [])
        if tools_used:
            print(f"\nTools used: {len(tools_used)}")
            for tool_use in tools_used:
                print(f"  - {tool_use['tool']} (turn {tool_use['turn']})")

        print("\n" + "=" * 80)
        print("Multi-Turn Assistant working successfully!")
        print("Natural tool calling pattern demonstrated!")
        print("=" * 80)

    except Exception as e:
        print(f"Error: {e}")
        print("\nMake sure all services are running:")
        print("1. Flux server: poetry run flux start server")
        print("2. Flux worker: poetry run flux start worker worker-1")
        print("3. Flux MCP server: poetry run flux start mcp")
        print("4. Ollama: ollama serve")
        print("5. Ollama model: ollama pull llama3.2")
