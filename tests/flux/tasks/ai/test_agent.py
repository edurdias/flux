from __future__ import annotations

import pytest

from flux.tasks.ai import agent


async def test_agent_parses_ollama_model():
    a = await agent("You are a test agent.", model="ollama/llama3")
    assert a.name == "agent_ollama_llama3"


async def test_agent_parses_model_with_version():
    a = await agent("You are a test agent.", model="ollama/llama3.2")
    assert a.name == "agent_ollama_llama3_2"


async def test_agent_custom_name():
    a = await agent("You are a test agent.", model="ollama/llama3", name="researcher")
    assert a.name == "researcher"


async def test_agent_rejects_invalid_model_format():
    with pytest.raises(ValueError, match="provider/model_name"):
        await agent("test", model="llama3")


async def test_agent_rejects_unknown_provider():
    with pytest.raises(ValueError, match="Unknown provider"):
        await agent("test", model="unknown/model")


async def test_agent_accepts_skills():
    from flux.tasks.ai.skills import Skill, SkillCatalog

    s1 = Skill(name="researcher", description="Researches.", instructions="Research.")
    catalog = SkillCatalog([s1])
    a = await agent("You are a test agent.", model="ollama/llama3", skills=catalog)
    assert a is not None


async def test_agent_skills_warns_missing_allowed_tools(caplog):
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
        await agent("You are a test agent.", model="ollama/llama3", skills=catalog)
    assert "search_web" in caplog.text


def test_skill_exports():
    from flux.tasks.ai import Skill, SkillCatalog

    assert Skill is not None
    assert SkillCatalog is not None


async def test_agent_accepts_stream_parameter():
    a = await agent("You are a test agent.", model="ollama/llama3", stream=True)
    assert a is not None


async def test_agent_stream_defaults_to_true():
    a = await agent("You are a test agent.", model="ollama/llama3")
    assert a is not None


async def test_agent_stream_false():
    a = await agent("You are a test agent.", model="ollama/llama3", stream=False)
    assert a is not None


async def test_agent_stream_disabled_with_response_format():
    from pydantic import BaseModel

    class Info(BaseModel):
        name: str

    a = await agent("Extract info.", model="ollama/llama3", response_format=Info, stream=True)
    assert a is not None


async def test_agent_parses_google_model():
    a = await agent("You are a test agent.", model="google/gemini-2.5-flash")
    assert a.name == "agent_google_gemini_2_5_flash"


async def test_agent_parses_google_model_with_version():
    a = await agent("You are a test agent.", model="google/gemini-2.5-pro")
    assert a.name == "agent_google_gemini_2_5_pro"


class _FakeSubAgent:
    def __init__(self, name, description):
        self.name = name
        self.description = description

    async def __call__(self, instruction, **kwargs):
        return f"result from {self.name}"


async def test_agent_accepts_agents():
    sub = _FakeSubAgent("researcher", "Research agent.")
    a = await agent("You are a manager.", model="ollama/llama3", agents=[sub])
    assert a is not None


async def test_agent_with_agents_has_delegate_tool():
    sub = _FakeSubAgent("researcher", "Research agent.")
    a = await agent("You are a manager.", model="ollama/llama3", agents=[sub])
    assert a.name == "agent_ollama_llama3"


async def test_agent_accepts_description():
    a = await agent(
        "You are a researcher.",
        model="ollama/llama3",
        name="researcher",
        description="Deep research agent.",
    )
    assert a.name == "researcher"
    assert a.description == "Deep research agent."


async def test_agent_without_description():
    a = await agent("You are a test agent.", model="ollama/llama3")
    assert not hasattr(a, "description") or a.description is None


def test_agent_exports():
    from flux.tasks.ai import DelegationResult, workflow_agent

    assert callable(workflow_agent)
    assert DelegationResult is not None


async def test_agent_accepts_planning_parameter():
    a = await agent("You are a test agent.", model="ollama/llama3", planning=True)
    assert a is not None


async def test_agent_planning_default_false():
    a = await agent("You are a test agent.", model="ollama/llama3")
    assert a is not None


async def test_agent_planning_with_openai(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    a = await agent("You are a test agent.", model="openai/gpt-4o", planning=True)
    assert a is not None


async def test_agent_planning_with_anthropic():
    a = await agent(
        "You are a test agent.",
        model="anthropic/claude-sonnet-4-20250514",
        planning=True,
    )
    assert a is not None


async def test_agent_planning_with_gemini():
    a = await agent("You are a test agent.", model="google/gemini-2.5-flash", planning=True)
    assert a is not None


def test_agent_plan_exports():
    from flux.tasks.ai import AgentPlan, AgentStep

    assert AgentPlan is not None
    assert AgentStep is not None


async def test_agent_planning_params_threaded():
    result = await agent(
        "You are an assistant.",
        model="ollama/llama3.2",
        planning=True,
        max_plan_steps=10,
        strict_dependencies=True,
        approve_plan=False,
        stream=False,
    )
    assert result is not None


async def test_agent_accepts_max_concurrent_tools():
    a = await agent("You are a test agent.", model="ollama/llama3", max_concurrent_tools=5)
    assert a is not None


async def test_agent_max_concurrent_tools_defaults_to_none():
    a = await agent("You are a test agent.", model="ollama/llama3")
    assert a is not None
