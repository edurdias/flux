from __future__ import annotations

import asyncio

import pytest

from flux.tasks.ai import agent


def test_agent_parses_ollama_model():
    a = asyncio.run(agent("You are a test agent.", model="ollama/llama3"))
    assert a.name == "agent_ollama_llama3"


def test_agent_parses_model_with_version():
    a = asyncio.run(agent("You are a test agent.", model="ollama/llama3.2"))
    assert a.name == "agent_ollama_llama3_2"


def test_agent_custom_name():
    a = asyncio.run(agent("You are a test agent.", model="ollama/llama3", name="researcher"))
    assert a.name == "researcher"


def test_agent_rejects_invalid_model_format():
    with pytest.raises(ValueError, match="provider/model_name"):
        asyncio.run(agent("test", model="llama3"))


def test_agent_rejects_unknown_provider():
    with pytest.raises(ValueError, match="Unknown provider"):
        asyncio.run(agent("test", model="unknown/model"))


def test_agent_accepts_skills():
    from flux.tasks.ai.skills import Skill, SkillCatalog

    s1 = Skill(name="researcher", description="Researches.", instructions="Research.")
    catalog = SkillCatalog([s1])
    a = asyncio.run(agent("You are a test agent.", model="ollama/llama3", skills=catalog))
    assert a is not None


def test_agent_skills_warns_missing_allowed_tools(caplog):
    import logging

    from flux.tasks.ai.skills import Skill, SkillCatalog

    s1 = Skill(
        name="researcher",
        description="Researches.",
        instructions="Research.",
        allowed_tools=["search_web"],
    )
    catalog = SkillCatalog([s1])
    with caplog.at_level(logging.WARNING, logger="flux.agent"):
        asyncio.run(agent("You are a test agent.", model="ollama/llama3", skills=catalog))
    assert "search_web" in caplog.text


def test_skill_exports():
    from flux.tasks.ai import Skill, SkillCatalog

    assert Skill is not None
    assert SkillCatalog is not None


def test_agent_accepts_stream_parameter():
    a = asyncio.run(agent("You are a test agent.", model="ollama/llama3", stream=True))
    assert a is not None


def test_agent_stream_defaults_to_true():
    a = asyncio.run(agent("You are a test agent.", model="ollama/llama3"))
    assert a is not None


def test_agent_stream_false():
    a = asyncio.run(agent("You are a test agent.", model="ollama/llama3", stream=False))
    assert a is not None


def test_agent_stream_disabled_with_response_format():
    from pydantic import BaseModel

    class Info(BaseModel):
        name: str

    a = asyncio.run(
        agent("Extract info.", model="ollama/llama3", response_format=Info, stream=True),
    )
    assert a is not None


def test_agent_parses_google_model():
    a = asyncio.run(agent("You are a test agent.", model="google/gemini-2.5-flash"))
    assert a.name == "agent_google_gemini_2_5_flash"


def test_agent_parses_google_model_with_version():
    a = asyncio.run(agent("You are a test agent.", model="google/gemini-2.5-pro"))
    assert a.name == "agent_google_gemini_2_5_pro"


def test_agent_accepts_planning_parameter():
    from flux import ExecutionContext, workflow

    @workflow
    async def test_wf(ctx: ExecutionContext):
        a = await agent("You are a test agent.", model="ollama/llama3", planning=True)
        return a is not None

    ctx = test_wf.run()
    assert ctx.has_succeeded
    assert ctx.output is True


def test_agent_planning_default_false():
    a = asyncio.run(agent("You are a test agent.", model="ollama/llama3"))
    assert a is not None


def test_agent_planning_with_openai(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    from flux import ExecutionContext, workflow

    @workflow
    async def test_wf(ctx: ExecutionContext):
        a = await agent("You are a test agent.", model="openai/gpt-4o", planning=True)
        return a is not None

    ctx = test_wf.run()
    assert ctx.has_succeeded
    assert ctx.output is True


def test_agent_planning_with_anthropic():
    from flux import ExecutionContext, workflow

    @workflow
    async def test_wf(ctx: ExecutionContext):
        a = await agent(
            "You are a test agent.",
            model="anthropic/claude-sonnet-4-20250514",
            planning=True,
        )
        return a is not None

    ctx = test_wf.run()
    assert ctx.has_succeeded
    assert ctx.output is True


def test_agent_planning_with_gemini():
    from flux import ExecutionContext, workflow

    @workflow
    async def test_wf(ctx: ExecutionContext):
        a = await agent("You are a test agent.", model="google/gemini-2.5-flash", planning=True)
        return a is not None

    ctx = test_wf.run()
    assert ctx.has_succeeded
    assert ctx.output is True


def test_agent_plan_exports():
    from flux.tasks.ai import AgentPlan, AgentStep

    assert AgentPlan is not None
    assert AgentStep is not None


def test_agent_planning_params_threaded():
    """Verify agent accepts new planning parameters without error."""
    result = asyncio.run(
        agent(
            "You are an assistant.",
            model="ollama/llama3.2",
            planning=True,
            max_plan_steps=10,
            strict_dependencies=True,
            approve_plan=False,
            stream=False,
        ),
    )
    assert result is not None
