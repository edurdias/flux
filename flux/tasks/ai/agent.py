from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from pydantic import BaseModel

from flux.task import task

if TYPE_CHECKING:
    from flux.tasks.ai.memory.long_term_memory import LongTermMemory
    from flux.tasks.ai.memory.working_memory import WorkingMemory
    from flux.tasks.ai.skills import SkillCatalog

logger = logging.getLogger("flux.agent")


async def agent(
    system_prompt: str,
    *,
    model: str,
    name: str | None = None,
    tools: list[task] | None = None,
    skills: SkillCatalog | None = None,
    planning: bool = False,
    response_format: type[BaseModel] | None = None,
    working_memory: WorkingMemory | None = None,
    long_term_memory: LongTermMemory | None = None,
    max_tool_calls: int = 10,
    max_tokens: int = 4096,
    stream: bool = True,
) -> task:
    """Create a Flux @task that calls an LLM.

    Parses the model string ("provider/model_name") to select the right provider
    and returns a task that can be awaited within a Flux workflow.

    Args:
        system_prompt: The system prompt defining the agent's identity.
        model: Provider and model in "provider/model_name" format.
        name: Task name for events/traces. Defaults to "agent_{provider}_{model}".
        tools: List of Flux @task functions the agent can call as tools.
        skills: SkillCatalog providing Agent Skills the LLM can activate.
        planning: If True, inject planning tools (create_plan, complete_step, get_plan)
            so the agent can create structured plans for complex tasks.
        response_format: Pydantic BaseModel subclass for structured JSON output.
        working_memory: WorkingMemory instance for conversation history across invocations.
        long_term_memory: LongTermMemory instance for persistent fact storage.
        max_tool_calls: Maximum tool call iterations before forcing a final answer.
        max_tokens: Maximum tokens in the LLM response (used by Anthropic and Google, ignored by others).
        stream: If True, enable streaming responses. Automatically disabled when response_format is set.

    Returns:
        A Flux @task callable with signature (instruction: str, *, context: str = "") -> str | BaseModel
    """
    if skills is not None:
        from flux.tasks.ai.skills import build_skills_preamble, build_use_skill

        system_prompt = system_prompt + build_skills_preamble(skills)
        use_skill_task = build_use_skill(skills)
        tools = (tools or []) + [use_skill_task]

    if long_term_memory is not None:
        tools = (tools or []) + long_term_memory.as_tools()
        system_prompt = system_prompt + long_term_memory.system_prompt_hint()

    plan_summary_fn = None
    if planning:
        from flux.tasks.ai.agent_plan import build_plan_preamble, build_plan_tools

        system_prompt = system_prompt + build_plan_preamble()
        plan_tools, plan_summary_fn = await build_plan_tools(
            long_term_memory=long_term_memory,
        )
        tools = (tools or []) + plan_tools

    if skills is not None:
        tool_names = {
            getattr(getattr(t, "func", None), "__name__", None) or getattr(t, "__name__", "")
            for t in (tools or [])
        }
        for skill in skills.list():
            for allowed in skill.allowed_tools:
                if allowed not in tool_names:
                    logger.warning(
                        "Skill '%s' declares allowed_tool '%s' which is not "
                        "in the agent's tools list.",
                        skill.name,
                        allowed,
                    )

    effective_stream = stream and response_format is None

    if "/" not in model:
        raise ValueError(
            f"Model must be in 'provider/model_name' format, got: '{model}'. "
            "Examples: 'ollama/llama3', 'openai/gpt-4o', 'anthropic/claude-sonnet-4-20250514', "
            "'google/gemini-2.5-flash'",
        )

    provider, model_name = model.split("/", 1)

    if provider == "ollama":
        from flux.tasks.ai.ollama import build_ollama_agent

        return build_ollama_agent(
            system_prompt=system_prompt,
            model_name=model_name,
            name=name,
            tools=tools,
            response_format=response_format,
            working_memory=working_memory,
            max_tool_calls=max_tool_calls,
            stream=effective_stream,
            plan_summary_fn=plan_summary_fn,
        )
    elif provider == "openai":
        from flux.tasks.ai.openai import build_openai_agent

        return build_openai_agent(
            system_prompt=system_prompt,
            model_name=model_name,
            name=name,
            tools=tools,
            response_format=response_format,
            working_memory=working_memory,
            max_tool_calls=max_tool_calls,
            stream=effective_stream,
            plan_summary_fn=plan_summary_fn,
        )
    elif provider == "anthropic":
        from flux.tasks.ai.anthropic import build_anthropic_agent

        return build_anthropic_agent(
            system_prompt=system_prompt,
            model_name=model_name,
            name=name,
            tools=tools,
            response_format=response_format,
            working_memory=working_memory,
            max_tool_calls=max_tool_calls,
            max_tokens=max_tokens,
            stream=effective_stream,
            plan_summary_fn=plan_summary_fn,
        )
    elif provider == "google":
        from flux.tasks.ai.gemini import build_gemini_agent

        return build_gemini_agent(
            system_prompt=system_prompt,
            model_name=model_name,
            name=name,
            tools=tools,
            response_format=response_format,
            working_memory=working_memory,
            max_tool_calls=max_tool_calls,
            max_tokens=max_tokens,
            stream=effective_stream,
            plan_summary_fn=plan_summary_fn,
        )
    else:
        raise ValueError(
            f"Unknown provider: '{provider}'. "
            "Supported providers: ollama, openai, anthropic, google",
        )
