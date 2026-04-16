from __future__ import annotations

import json
from typing import Any

from flux.domain.execution_context import ExecutionContext
from flux.tasks.config_task import get_config
from flux.tasks.pause import pause
from flux.workflow import workflow

from flux.agents.tools_resolver import resolve_builtin_tools
from flux.agents.types import ChatResponseOutput


@workflow.with_options(namespace="agents")
async def agent_chat(ctx: ExecutionContext[dict[str, Any]]):
    agent_name = ctx.input["agent"]

    config_raw = await get_config(f"agent:{agent_name}")
    agent_def = json.loads(config_raw) if isinstance(config_raw, str) else config_raw

    tools = resolve_builtin_tools(agent_def.get("tools", []))

    from flux.tasks.ai import agent
    from flux.tasks.ai.memory import working_memory

    wm = working_memory(window=agent_def.get("memory_window", 50))

    chatbot = await agent(
        system_prompt=agent_def["system_prompt"],
        model=agent_def["model"],
        tools=tools or None,
        working_memory=wm,
        planning=agent_def.get("planning", False),
        max_plan_steps=agent_def.get("max_plan_steps", 20),
        approve_plan=agent_def.get("approve_plan", False),
        max_tool_calls=agent_def.get("max_tool_calls", 10),
        max_concurrent_tools=agent_def.get("max_concurrent_tools"),
        max_tokens=agent_def.get("max_tokens", 4096),
        stream=agent_def.get("stream", True),
        approval_mode=agent_def.get("approval_mode", "default"),
        reasoning_effort=agent_def.get("reasoning_effort"),
    )

    turn = 0
    response = None

    while True:
        output = ChatResponseOutput(content=response, turn=turn).model_dump()
        next_input = await pause(f"turn_{turn}", output=output)
        response = await chatbot(next_input["message"])
        turn += 1
