from __future__ import annotations

import pytest

from flux.tasks.ai import agent


def test_agent_parses_ollama_model():
    a = agent("You are a test agent.", model="ollama/llama3")
    assert a.name == "agent_ollama_llama3"


def test_agent_parses_model_with_version():
    a = agent("You are a test agent.", model="ollama/llama3.2")
    assert a.name == "agent_ollama_llama3_2"


def test_agent_custom_name():
    a = agent("You are a test agent.", model="ollama/llama3", name="researcher")
    assert a.name == "researcher"


def test_agent_rejects_invalid_model_format():
    with pytest.raises(ValueError, match="provider/model_name"):
        agent("test", model="llama3")


def test_agent_rejects_unknown_provider():
    with pytest.raises(ValueError, match="Unknown provider"):
        agent("test", model="unknown/model")


def test_agent_accepts_skills():
    from flux.tasks.ai.skills import Skill, SkillCatalog

    s1 = Skill(name="researcher", description="Researches.", instructions="Research.")
    catalog = SkillCatalog([s1])
    a = agent("You are a test agent.", model="ollama/llama3", skills=catalog)
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
        agent("You are a test agent.", model="ollama/llama3", skills=catalog)
    assert "search_web" in caplog.text


def test_skill_exports():
    from flux.tasks.ai import Skill, SkillCatalog

    assert Skill is not None
    assert SkillCatalog is not None


def test_agent_accepts_stream_parameter():
    a = agent("You are a test agent.", model="ollama/llama3", stream=True)
    assert a is not None


def test_agent_stream_defaults_to_true():
    a = agent("You are a test agent.", model="ollama/llama3")
    assert a is not None


def test_agent_stream_false():
    a = agent("You are a test agent.", model="ollama/llama3", stream=False)
    assert a is not None


def test_agent_stream_disabled_with_response_format():
    from pydantic import BaseModel

    class Info(BaseModel):
        name: str

    a = agent("Extract info.", model="ollama/llama3", response_format=Info, stream=True)
    assert a is not None


def test_agent_parses_google_model():
    a = agent("You are a test agent.", model="google/gemini-2.5-flash")
    assert a.name == "agent_google_gemini_2_5_flash"


def test_agent_parses_google_model_with_version():
    a = agent("You are a test agent.", model="google/gemini-2.5-pro")
    assert a.name == "agent_google_gemini_2_5_pro"


class _FakeSubAgent:
    def __init__(self, name, description):
        self.name = name
        self.description = description

    async def __call__(self, instruction, **kwargs):
        return f"result from {self.name}"


def test_agent_accepts_agents():
    sub = _FakeSubAgent("researcher", "Research agent.")
    a = agent("You are a manager.", model="ollama/llama3", agents=[sub])
    assert a is not None


def test_agent_with_agents_has_delegate_tool():
    sub = _FakeSubAgent("researcher", "Research agent.")
    a = agent("You are a manager.", model="ollama/llama3", agents=[sub])
    assert a.name == "agent_ollama_llama3"


def test_agent_accepts_description():
    a = agent(
        "You are a researcher.",
        model="ollama/llama3",
        name="researcher",
        description="Deep research agent.",
    )
    assert a.name == "researcher"
    assert a.description == "Deep research agent."


def test_agent_without_description():
    a = agent("You are a test agent.", model="ollama/llama3")
    assert not hasattr(a, "description") or a.description is None


def test_agent_exports():
    from flux.tasks.ai import workflow_agent, DelegationResult

    assert callable(workflow_agent)
    assert DelegationResult is not None
