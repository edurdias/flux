"""Tests for agent definition and pause output types."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from flux.agents.types import (
    AgentDefinition,
    ChatResponseOutput,
    SessionEndOutput,
)


class TestAgentDefinition:
    def test_minimal_definition(self):
        agent = AgentDefinition(
            name="test",
            model="anthropic/claude-sonnet-4-20250514",
            system_prompt="You are helpful.",
        )
        assert agent.name == "test"
        assert agent.model == "anthropic/claude-sonnet-4-20250514"
        assert agent.system_prompt == "You are helpful."

    def test_defaults(self):
        agent = AgentDefinition(
            name="test",
            model="openai/gpt-4o",
            system_prompt="Help.",
        )
        assert agent.tools == []
        assert agent.mcp_servers == []
        assert agent.agents == []
        assert agent.planning is False
        assert agent.max_plan_steps == 20
        assert agent.approve_plan is False
        assert agent.max_tool_calls == 10
        assert agent.max_concurrent_tools is None
        assert agent.max_tokens == 4096
        assert agent.stream is True
        assert agent.approval_mode == "default"
        assert agent.reasoning_effort is None
        assert agent.long_term_memory is None
        assert agent.tools_file is None
        assert agent.workflow_file is None
        assert agent.skills_dir is None
        assert agent.description is None

    def test_full_definition(self):
        agent = AgentDefinition(
            name="coder",
            model="anthropic/claude-sonnet-4-20250514",
            system_prompt="You are a coder.",
            description="A coding assistant",
            tools=[{"system_tools": {"workspace": "/home/user", "timeout": 60}}],
            tools_file="./tools.py",
            workflow_file="./workflow.py",
            mcp_servers=[{"url": "http://localhost:8080", "name": "github"}],
            skills_dir="./skills",
            agents=["researcher"],
            planning=True,
            max_plan_steps=30,
            approve_plan=True,
            max_tool_calls=20,
            max_concurrent_tools=5,
            max_tokens=8192,
            stream=True,
            approval_mode="always",
            reasoning_effort="high",
            long_term_memory={
                "provider": "sqlite",
                "connection": "memory.db",
                "scope": "user:default",
            },
        )
        assert agent.name == "coder"
        assert agent.planning is True
        assert agent.max_plan_steps == 30

    def test_model_format_validation(self):
        with pytest.raises(ValidationError):
            AgentDefinition(
                name="bad",
                model="no-slash-here",
                system_prompt="Help.",
            )

    def test_reasoning_effort_validation(self):
        with pytest.raises(ValidationError):
            AgentDefinition(
                name="bad",
                model="openai/gpt-4o",
                system_prompt="Help.",
                reasoning_effort="invalid",
            )

    def test_long_term_memory_requires_connection(self):
        with pytest.raises(ValidationError, match="long_term_memory.connection"):
            AgentDefinition(
                name="bad",
                model="openai/gpt-4o",
                system_prompt="Help.",
                long_term_memory={"provider": "sqlite"},
            )

    def test_long_term_memory_with_connection_accepted(self):
        agent = AgentDefinition(
            name="ok",
            model="openai/gpt-4o",
            system_prompt="Help.",
            long_term_memory={"provider": "sqlite", "connection": "memory.db"},
        )
        assert agent.long_term_memory["connection"] == "memory.db"

    def test_mutable_defaults_are_independent(self):
        a = AgentDefinition(name="a", model="openai/gpt-4o", system_prompt="A")
        b = AgentDefinition(name="b", model="openai/gpt-4o", system_prompt="B")
        a.tools.append("shell")
        assert b.tools == []
        a.mcp_servers.append({"url": "http://x"})
        assert b.mcp_servers == []
        a.agents.append("sub")
        assert b.agents == []

    def test_serialization_roundtrip(self):
        agent = AgentDefinition(
            name="test",
            model="openai/gpt-4o",
            system_prompt="Help.",
            planning=True,
        )
        data = agent.model_dump()
        restored = AgentDefinition(**data)
        assert restored == agent

    def test_from_yaml_dict(self):
        yaml_dict = {
            "name": "coder",
            "model": "anthropic/claude-sonnet-4-20250514",
            "system_prompt": "You are a coder.",
            "tools": ["shell", "read_file"],
            "planning": True,
        }
        agent = AgentDefinition(**yaml_dict)
        assert agent.name == "coder"
        assert agent.tools == ["shell", "read_file"]


class TestChatResponseOutput:
    def test_chat_response(self):
        output = ChatResponseOutput(content="Hello!", turn=1)
        assert output.type == "chat_response"
        assert output.content == "Hello!"
        assert output.turn == 1

    def test_chat_response_none_content(self):
        output = ChatResponseOutput(content=None, turn=0)
        assert output.content is None

    def test_serialization(self):
        output = ChatResponseOutput(content="Hi", turn=2)
        data = output.model_dump()
        assert data["type"] == "chat_response"
        assert data["content"] == "Hi"
        assert data["turn"] == 2


class TestSessionEndOutput:
    def test_session_end(self):
        output = SessionEndOutput(reason="max_turns", turns=10)
        assert output.type == "session_end"
        assert output.reason == "max_turns"
        assert output.turns == 10

    def test_serialization(self):
        output = SessionEndOutput(reason="user_exit", turns=5)
        data = output.model_dump()
        assert data["type"] == "session_end"
