"""Tests for agent tools resolver."""

from __future__ import annotations

import pytest

from flux.agents.tools_resolver import resolve_builtin_tools


def test_resolve_system_tools():
    tools = resolve_builtin_tools(
        [{"system_tools": {"workspace": "/tmp", "timeout": 30}}]
    )
    assert len(tools) > 0
    tool_names = [t.name for t in tools]
    assert "shell" in tool_names or any("shell" in n for n in tool_names)


def test_resolve_individual_tool_group():
    tools = resolve_builtin_tools([{"shell": {"workspace": "/tmp"}}])
    assert len(tools) > 0


def test_resolve_empty_tools():
    tools = resolve_builtin_tools([])
    assert tools == []


def test_resolve_mixed_tools():
    tools = resolve_builtin_tools(
        [
            {"shell": {"workspace": "/tmp"}},
            {"files": {"workspace": "/tmp"}},
        ]
    )
    assert len(tools) > 0


def test_resolve_unknown_tool_raises():
    with pytest.raises(ValueError, match="Unknown"):
        resolve_builtin_tools([{"unknown_toolset": {}}])
