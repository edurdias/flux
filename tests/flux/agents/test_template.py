"""Tests for agent chat workflow template."""

from __future__ import annotations

from flux.agents.template import agent_chat


def test_template_exists():
    assert agent_chat is not None


def test_template_is_workflow():
    assert hasattr(agent_chat, "__wrapped__") or callable(agent_chat)
