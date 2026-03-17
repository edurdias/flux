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
