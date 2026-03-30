from __future__ import annotations

from pathlib import Path

import pytest

from flux.tasks.ai.tools import system_tools
from flux.tasks.ai.tools.system_tools import DEFAULT_BLOCKLIST, SystemToolsConfig


def test_system_tools_returns_list_of_tasks(tmp_path):
    tools = system_tools(workspace=tmp_path)
    assert isinstance(tools, list)
    assert len(tools) == 9


def test_system_tools_requires_workspace():
    with pytest.raises(TypeError):
        system_tools()


def test_system_tools_resolves_workspace(tmp_path):
    sub = tmp_path / "a" / "b"
    sub.mkdir(parents=True)
    tools = system_tools(workspace=str(sub))
    assert isinstance(tools, list)


def test_system_tools_accepts_string_workspace(tmp_path):
    tools = system_tools(workspace=str(tmp_path))
    assert len(tools) == 9


def test_system_tools_accepts_path_workspace(tmp_path):
    tools = system_tools(workspace=tmp_path)
    assert len(tools) == 9


def test_system_tools_default_blocklist(tmp_path):
    tools = system_tools(workspace=tmp_path)
    assert len(tools) == 9


def test_system_tools_custom_blocklist(tmp_path):
    tools = system_tools(workspace=tmp_path, blocklist=[r"custom"])
    assert len(tools) == 9


def test_system_tools_empty_blocklist(tmp_path):
    tools = system_tools(workspace=tmp_path, blocklist=[])
    assert len(tools) == 9


def test_config_dataclass():
    config = SystemToolsConfig(
        workspace=Path("/tmp"),
        timeout=60,
        blocklist=[],
        max_output_chars=50_000,
    )
    assert config.timeout == 60
    assert config.max_output_chars == 50_000


def test_default_blocklist_is_nonempty():
    assert len(DEFAULT_BLOCKLIST) > 0
