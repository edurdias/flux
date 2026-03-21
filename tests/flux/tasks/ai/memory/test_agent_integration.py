from __future__ import annotations

import pytest

from flux.tasks.ai import agent
from flux.tasks.ai.memory import in_memory, long_term_memory, working_memory


def test_agent_accepts_working_memory():
    wm = working_memory()
    a = agent("You are a test agent.", model="ollama/llama3", working_memory=wm)
    assert a is not None


def test_agent_accepts_long_term_memory():
    ltm = long_term_memory(provider=in_memory(), scope="test")
    a = agent("You are a test agent.", model="ollama/llama3", long_term_memory=ltm)
    assert a is not None


def test_agent_accepts_both_memories():
    wm = working_memory(window=10)
    ltm = long_term_memory(provider=in_memory(), scope="test")
    a = agent(
        "You are a test agent.",
        model="ollama/llama3",
        working_memory=wm,
        long_term_memory=ltm,
    )
    assert a is not None


def test_agent_no_stateful_parameter():
    """stateful parameter should no longer exist."""
    with pytest.raises(TypeError):
        agent("test", model="ollama/llama3", stateful=True)


def test_agent_ltm_injects_tools():
    """Long-term memory should add memory tools to the agent."""
    ltm = long_term_memory(provider=in_memory(), scope="test")
    a = agent("You are a test agent.", model="ollama/llama3", long_term_memory=ltm)
    assert a is not None
