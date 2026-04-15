"""Tests for agent definition manager."""

from __future__ import annotations

import pytest

from flux.agents.manager import AgentManager
from flux.agents.types import AgentDefinition


@pytest.fixture
def manager():
    return AgentManager.current()


@pytest.fixture(autouse=True)
def _cleanup(manager):
    yield
    for name in ["test_agent", "update_agent", "list_agent_1", "list_agent_2", "delete_agent"]:
        try:
            manager.delete(name)
        except Exception:
            pass


def test_create_and_get(manager):
    definition = AgentDefinition(
        name="test_agent",
        model="openai/gpt-4o",
        system_prompt="You are helpful.",
    )
    manager.create(definition)
    result = manager.get("test_agent")
    assert result.name == "test_agent"
    assert result.model == "openai/gpt-4o"
    assert result.system_prompt == "You are helpful."


def test_create_duplicate_raises(manager):
    definition = AgentDefinition(
        name="test_agent",
        model="openai/gpt-4o",
        system_prompt="Help.",
    )
    manager.create(definition)
    with pytest.raises(ValueError, match="already exists"):
        manager.create(definition)


def test_get_nonexistent_raises(manager):
    with pytest.raises(ValueError, match="not found"):
        manager.get("nonexistent")


def test_update(manager):
    definition = AgentDefinition(
        name="update_agent",
        model="openai/gpt-4o",
        system_prompt="Original.",
    )
    manager.create(definition)

    definition.system_prompt = "Updated."
    definition.planning = True
    manager.update(definition)

    result = manager.get("update_agent")
    assert result.system_prompt == "Updated."
    assert result.planning is True


def test_update_nonexistent_raises(manager):
    definition = AgentDefinition(
        name="nonexistent",
        model="openai/gpt-4o",
        system_prompt="Help.",
    )
    with pytest.raises(ValueError, match="not found"):
        manager.update(definition)


def test_delete(manager):
    definition = AgentDefinition(
        name="delete_agent",
        model="openai/gpt-4o",
        system_prompt="Help.",
    )
    manager.create(definition)
    manager.delete("delete_agent")
    with pytest.raises(ValueError):
        manager.get("delete_agent")


def test_delete_nonexistent_raises(manager):
    with pytest.raises(ValueError, match="not found"):
        manager.delete("nonexistent")


def test_list_agents(manager):
    for name in ["list_agent_1", "list_agent_2"]:
        manager.create(
            AgentDefinition(
                name=name,
                model="openai/gpt-4o",
                system_prompt="Help.",
            )
        )
    result = manager.list()
    names = [a.name for a in result]
    assert "list_agent_1" in names
    assert "list_agent_2" in names


def test_create_with_full_config(manager):
    definition = AgentDefinition(
        name="test_agent",
        model="anthropic/claude-sonnet-4-20250514",
        system_prompt="You are a coder.",
        description="Coding assistant",
        tools=[{"system_tools": {"workspace": "/tmp", "timeout": 30}}],
        planning=True,
        max_tool_calls=20,
        reasoning_effort="high",
        long_term_memory={"provider": "sqlite", "scope": "default"},
    )
    manager.create(definition)
    result = manager.get("test_agent")
    assert result.description == "Coding assistant"
    assert result.planning is True
    assert result.max_tool_calls == 20
    assert result.reasoning_effort == "high"
    assert result.long_term_memory == {"provider": "sqlite", "scope": "default"}
    assert result.tools == [{"system_tools": {"workspace": "/tmp", "timeout": 30}}]
