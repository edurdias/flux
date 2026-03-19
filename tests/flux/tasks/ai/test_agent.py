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
