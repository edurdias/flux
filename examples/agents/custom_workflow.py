"""Custom agent workflow — referenced via workflow_file in the agent YAML.

Custom workflows must follow the agent workflow contract:
- Input (first run):  {"agent": "<name>"}
- Input (resume):     {"message": "<user text>"}
- Pause output:       {"type": "chat_response", "content": ..., "turn": N}
- Session end:        {"type": "session_end", "reason": ..., "turns": N}

This example adds a welcome message and a turn limit.

Usage:
    flux agent create my-agent \
        --model anthropic/claude-sonnet-4-20250514 \
        --system-prompt "You are a helpful assistant." \
        --workflow-file examples/agents/custom_workflow.py
"""

from flux import workflow, ExecutionContext, pause
from flux.tasks.ai import agent
from flux.tasks.config_task import get_config


MAX_TURNS = 50


@workflow.with_options(namespace="agents")
async def agent_chat(ctx: ExecutionContext):
    agent_def = await get_config(f"agent:{ctx.input['agent']}")

    chatbot = await agent(
        model=agent_def["model"],
        system_prompt=agent_def["system_prompt"],
        stream=agent_def.get("stream", True),
        max_tool_calls=agent_def.get("max_tool_calls", 10),
        max_tokens=agent_def.get("max_tokens", 4096),
    )

    turn = 0
    response = f"Hello! I'm **{agent_def.get('description', 'your assistant')}**. How can I help?"

    while turn < MAX_TURNS:
        next_input = await pause(
            f"turn_{turn}",
            output={"type": "chat_response", "content": response, "turn": turn},
        )
        message = next_input.get("message", "")
        if message.lower() in ("/quit", "/exit", "bye"):
            break
        response = await chatbot(message)
        turn += 1

    await pause(
        "session_end",
        output={
            "type": "session_end",
            "reason": "max_turns" if turn >= MAX_TURNS else "user_exit",
            "turns": turn,
        },
    )
