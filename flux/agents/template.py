from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from flux.domain.execution_context import ExecutionContext
from flux.tasks.config_task import get_config
from flux.tasks.pause import pause
from flux.workflow import workflow

from flux.agents.tools_resolver import resolve_builtin_tools
from flux.agents.types import ChatResponseOutput


def _materialize_skills_bundle(tmp: Path, skills_data: dict[str, Any]) -> None:
    """Write a skills bundle into ``tmp``, rejecting any path that escapes it.

    The bundle is sourced from agent definitions in the database and is therefore
    treated as untrusted: each ``file_path`` must be a relative path that resolves
    inside ``tmp`` after path normalization.
    """
    base = tmp.resolve()
    for skill_name, files in skills_data.items():
        if not isinstance(files, dict):
            raise ValueError(
                f"Skill '{skill_name}' bundle must be a mapping of file paths to contents.",
            )
        for file_path, content in files.items():
            candidate = Path(file_path)
            if candidate.is_absolute() or any(part == ".." for part in candidate.parts):
                raise ValueError(
                    f"Skill '{skill_name}' contains unsafe file path: {file_path!r}",
                )
            full_path = (base / candidate).resolve()
            if not full_path.is_relative_to(base):
                raise ValueError(
                    f"Skill '{skill_name}' file path escapes bundle root: {file_path!r}",
                )
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(content)


@workflow.with_options(namespace="agents")
async def agent_chat(ctx: ExecutionContext[dict[str, Any]]):
    agent_name = ctx.input["agent"]

    config_raw = await get_config(f"agent:{agent_name}")
    agent_def = json.loads(config_raw) if isinstance(config_raw, str) else config_raw

    tools = resolve_builtin_tools(agent_def.get("tools", []))

    skills = None
    _skills_tmp_dir = None
    if agent_def.get("skills_dir"):
        import tempfile
        from flux.tasks.ai.skills import SkillCatalog

        skills_data = agent_def["skills_dir"]
        if isinstance(skills_data, str):
            try:
                skills_data = json.loads(skills_data)
            except (json.JSONDecodeError, ValueError):
                pass

        if isinstance(skills_data, dict):
            _skills_tmp_dir = tempfile.TemporaryDirectory(prefix="flux_skills_")
            tmp = Path(_skills_tmp_dir.name)
            _materialize_skills_bundle(tmp, skills_data)
            skills = SkillCatalog.from_directory(str(tmp))
        else:
            skills = SkillCatalog.from_directory(str(skills_data))

    ltm = None
    if agent_def.get("long_term_memory"):
        ltm_cfg = agent_def["long_term_memory"]
        from flux.tasks.ai.memory import long_term_memory as build_ltm
        from flux.tasks.ai.memory import sqlite as sqlite_provider

        provider_name = ltm_cfg.get("provider", "sqlite")
        connection = ltm_cfg.get("connection")
        if not connection:
            raise ValueError("long_term_memory.connection is required")

        if provider_name == "sqlite":
            provider = sqlite_provider(connection)
        elif provider_name == "postgresql":
            from flux.tasks.ai.memory import postgresql as pg_provider

            provider = pg_provider(connection)
        else:
            raise ValueError(f"Unknown long_term_memory provider: {provider_name}")

        ltm = build_ltm(provider=provider, agent=agent_name, scope=ltm_cfg.get("scope", "default"))

    sub_agents = None
    if agent_def.get("agents"):
        from flux.tasks.ai.delegation import workflow_agent

        sub_agents = []
        for sub_name in agent_def["agents"]:
            sub_def_raw = await get_config(f"agent:{sub_name}")
            sub_def = json.loads(sub_def_raw) if isinstance(sub_def_raw, str) else sub_def_raw
            sub_agents.append(
                workflow_agent(
                    name=sub_name,
                    description=sub_def.get("description", f"Agent: {sub_name}"),
                    workflow="agents/agent_chat",
                ),
            )

    _mcp_clients: list[Any] = []
    if agent_def.get("mcp_servers"):
        from flux.tasks.mcp import mcp, bearer
        from flux.secret_managers import SecretManager

        for srv in agent_def["mcp_servers"]:
            auth = None
            if srv.get("secret"):
                secrets = await SecretManager.current().get([srv["secret"]])
                auth = bearer(token=secrets[srv["secret"]])

            client = mcp(
                server=srv["url"],
                name=srv.get("name"),
                auth=auth,
            )
            _mcp_clients.append(client)
            toolset = await client.discover()
            tools.extend(list(toolset))

    if agent_def.get("tools_file"):
        import importlib.util
        import sys

        from flux.task import task as task_cls

        source = agent_def["tools_file"]
        mod_name = f"agent_tools_{agent_name}"
        mod_spec = importlib.util.spec_from_loader(mod_name, loader=None)
        if mod_spec is None:
            raise ValueError(f"Could not create module spec for {mod_name}")
        mod = importlib.util.module_from_spec(mod_spec)
        sys.modules[mod_name] = mod
        # Source is trusted: it was uploaded at workflow registration time,
        # gated behind the `workflow:*:*:register` permission, and stored
        # inline in the database (see docs/advanced-features/agent-harness.md).
        # Workers intentionally never read user code from the local filesystem,
        # so importlib.import_module is not an option here.
        exec(source, mod.__dict__)  # noqa: S102

        for obj in mod.__dict__.values():
            if isinstance(obj, task_cls):
                tools.append(obj)

    from flux.tasks.ai import agent
    from flux.tasks.ai.memory import working_memory

    wm = working_memory(window=agent_def.get("memory_window", 50))

    chatbot = await agent(
        system_prompt=agent_def["system_prompt"],
        model=agent_def["model"],
        tools=tools or None,
        skills=skills,
        agents=sub_agents,
        working_memory=wm,
        long_term_memory=ltm,
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

    try:
        while True:
            output = ChatResponseOutput(content=response, turn=turn).model_dump()
            next_input = await pause(f"turn_{turn}", output=output)
            response = await chatbot(next_input["message"])
            turn += 1
    finally:
        if _skills_tmp_dir is not None:
            _skills_tmp_dir.cleanup()
        for _client in _mcp_clients:
            try:
                await _client.__aexit__(None, None, None)
            except Exception:
                pass
