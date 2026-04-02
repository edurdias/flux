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
    description: str | None = None,
    tools: list[task] | None = None,
    skills: SkillCatalog | None = None,
    agents: list | None = None,
    planning: bool = False,
    max_plan_steps: int = 20,
    strict_dependencies: bool = False,
    approve_plan: bool = False,
    response_format: type[BaseModel] | None = None,
    working_memory: WorkingMemory | None = None,
    long_term_memory: LongTermMemory | None = None,
    max_tool_calls: int = 10,
    max_concurrent_tools: int | None = None,
    max_tokens: int = 4096,
    stream: bool = True,
    approval_mode: str = "default",
) -> task:
    """Create a Flux @task that calls an LLM.

    Parses the model string ("provider/model_name") to select the right provider
    and returns a task that can be awaited within a Flux workflow.

    Args:
        system_prompt: The system prompt defining the agent's identity.
        model: Provider and model in "provider/model_name" format.
        name: Task name for events/traces. Defaults to "agent_{provider}_{model}".
        description: Human-readable description of the agent, used when this agent
            is a sub-agent so the parent knows when to delegate to it.
        tools: List of Flux @task functions the agent can call as tools.
        skills: SkillCatalog providing Agent Skills the LLM can activate.
        agents: List of sub-agents this agent can delegate to via a ``delegate`` tool.
        planning: If True, inject planning tools (create_plan, start_step,
            mark_step_done, mark_step_failed, get_plan, get_ready_steps)
            so the agent can create structured plans for complex tasks.
        max_plan_steps: Maximum number of steps allowed in a plan. Defaults to 20.
        strict_dependencies: If True, prevent starting a step before its dependencies
            are completed. Defaults to False (warns instead).
        approve_plan: If True, pause for human approval before activating a new plan.
            Defaults to False.
        response_format: Pydantic BaseModel subclass for structured JSON output.
        working_memory: WorkingMemory instance for conversation history across invocations.
        long_term_memory: LongTermMemory instance for persistent fact storage.
        max_tool_calls: Maximum tool call iterations before forcing a final answer.
        max_concurrent_tools: Maximum number of tools to run concurrently when
            the LLM emits multiple tool calls in a single turn. None means
            unlimited. Defaults to None.
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

    if agents:
        from flux.tasks.ai.delegation import build_agents_preamble, build_delegate

        system_prompt = system_prompt + build_agents_preamble(agents)
        tools = (tools or []) + [build_delegate(agents)]

    plan_summary_fn = None
    if planning:
        from flux.tasks.ai.agent_plan import build_plan_preamble, build_plan_tools

        system_prompt = system_prompt + build_plan_preamble()
        plan_tools, plan_summary_fn = await build_plan_tools(
            strict_dependencies=strict_dependencies,
            max_plan_steps=max_plan_steps,
            approve_plan=approve_plan,
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

    if tools:
        from flux.tasks.ai.tool_executor import build_tools_preamble

        system_prompt = system_prompt + build_tools_preamble(tools, approval_mode=approval_mode)

    effective_stream = stream and response_format is None

    if "/" not in model:
        raise ValueError(
            f"Model must be in 'provider/model_name' format, got: '{model}'. "
            "Examples: 'ollama/llama3', 'openai/gpt-4o', 'anthropic/claude-sonnet-4-20250514', "
            "'google/gemini-2.5-flash'",
        )

    provider, model_name = model.split("/", 1)

    if provider == "ollama":
        from flux.tasks.ai.agent_loop import run_agent_loop
        from flux.tasks.ai.ollama import OllamaFormatter, _to_ollama_tools, build_ollama_provider
        from flux.tasks.ai.tool_executor import build_tool_schemas

        llm_task, formatter = build_ollama_provider(model_name, response_format=response_format)

        tool_schemas = build_tool_schemas(tools) if tools else None
        ollama_tools = _to_ollama_tools(tool_schemas) if tool_schemas else None

        if tool_schemas and isinstance(formatter, OllamaFormatter):
            ollama_tool_names: set[str] = {str(s["name"]) for s in tool_schemas}
            formatter.set_tool_names(ollama_tool_names)

        task_name = name or f"agent_ollama_{model_name.replace(':', '_').replace('.', '_')}"

        @task.with_options(name=task_name)
        async def _ollama_agent(instruction: str, *, context: str = "") -> str | BaseModel:
            return await run_agent_loop(
                llm_task=llm_task,
                formatter=formatter,
                system_prompt=system_prompt,
                instruction=instruction,
                context=context,
                tools=tools,
                tool_schemas=ollama_tools,
                response_format=response_format,
                working_memory=working_memory,
                max_tool_calls=max_tool_calls,
                max_concurrent_tools=max_concurrent_tools,
                stream=effective_stream,
                plan_summary_fn=plan_summary_fn,
                approval_mode=approval_mode,
            )

        result = _ollama_agent
    elif provider == "openai":
        from flux.tasks.ai.agent_loop import run_agent_loop
        from flux.tasks.ai.openai import _to_openai_tools, build_openai_provider
        from flux.tasks.ai.tool_executor import build_tool_schemas

        llm_task, formatter = build_openai_provider(model_name, response_format=response_format)

        tool_schemas = build_tool_schemas(tools) if tools else None
        openai_tools = _to_openai_tools(tool_schemas) if tool_schemas else None

        task_name = name or f"agent_openai_{model_name.replace('-', '_').replace('.', '_')}"

        @task.with_options(name=task_name)
        async def _openai_agent(instruction: str, *, context: str = "") -> str | BaseModel:
            return await run_agent_loop(
                llm_task=llm_task,
                formatter=formatter,
                system_prompt=system_prompt,
                instruction=instruction,
                context=context,
                tools=tools,
                tool_schemas=openai_tools,
                response_format=response_format,
                working_memory=working_memory,
                max_tool_calls=max_tool_calls,
                max_concurrent_tools=max_concurrent_tools,
                stream=effective_stream,
                plan_summary_fn=plan_summary_fn,
                approval_mode=approval_mode,
            )

        result = _openai_agent
    elif provider == "anthropic":
        from flux.tasks.ai.agent_loop import run_agent_loop
        from flux.tasks.ai.anthropic import _to_anthropic_tools, build_anthropic_provider
        from flux.tasks.ai.tool_executor import build_tool_schemas

        llm_task, formatter = build_anthropic_provider(model_name, max_tokens=max_tokens)

        tool_schemas = build_tool_schemas(tools) if tools else None
        anthropic_tools = _to_anthropic_tools(tool_schemas) if tool_schemas else None

        task_name = name or f"agent_anthropic_{model_name.replace('-', '_').replace('.', '_')}"

        @task.with_options(name=task_name)
        async def _anthropic_agent(instruction: str, *, context: str = "") -> str | BaseModel:
            return await run_agent_loop(
                llm_task=llm_task,
                formatter=formatter,
                system_prompt=system_prompt,
                instruction=instruction,
                context=context,
                tools=tools,
                tool_schemas=anthropic_tools,
                response_format=response_format,
                working_memory=working_memory,
                max_tool_calls=max_tool_calls,
                max_concurrent_tools=max_concurrent_tools,
                stream=effective_stream,
                plan_summary_fn=plan_summary_fn,
                approval_mode=approval_mode,
            )

        result = _anthropic_agent
    elif provider == "google":
        from flux.tasks.ai.agent_loop import run_agent_loop
        from flux.tasks.ai.gemini import _to_gemini_tools, build_gemini_provider
        from flux.tasks.ai.tool_executor import build_tool_schemas

        llm_task, formatter = build_gemini_provider(
            model_name,
            max_tokens=max_tokens,
            response_format=response_format,
        )

        tool_schemas = build_tool_schemas(tools) if tools else None
        gemini_tools = _to_gemini_tools(tool_schemas) if tool_schemas else None

        task_name = name or f"agent_google_{model_name.replace('-', '_').replace('.', '_')}"

        @task.with_options(name=task_name)
        async def _google_agent(instruction: str, *, context: str = "") -> str | BaseModel:
            return await run_agent_loop(
                llm_task=llm_task,
                formatter=formatter,
                system_prompt=system_prompt,
                instruction=instruction,
                context=context,
                tools=tools,
                tool_schemas=gemini_tools,
                response_format=response_format,
                working_memory=working_memory,
                max_tool_calls=max_tool_calls,
                max_concurrent_tools=max_concurrent_tools,
                stream=effective_stream,
                plan_summary_fn=plan_summary_fn,
                approval_mode=approval_mode,
            )

        result = _google_agent
    else:
        raise ValueError(
            f"Unknown provider: '{provider}'. "
            "Supported providers: ollama, openai, anthropic, google",
        )

    if description is not None:
        result.description = description

    return result
