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


def test_system_tools_has_all_expected_tools(tmp_path):
    tools = system_tools(workspace=tmp_path)
    names = {t.func.__name__ for t in tools}
    assert names == {
        "shell",
        "read_file",
        "write_file",
        "edit_file",
        "file_info",
        "find_files",
        "grep",
        "list_directory",
        "directory_tree",
    }


def test_system_tools_produce_valid_schemas(tmp_path):
    from flux.tasks.ai.tool_executor import build_tool_schemas

    tools = system_tools(workspace=tmp_path)
    schemas = build_tool_schemas(tools)
    assert len(schemas) == 9

    for schema in schemas:
        assert "name" in schema
        assert "description" in schema
        assert len(schema["description"]) > 0
        assert "parameters" in schema
        assert schema["parameters"]["type"] == "object"


def test_shell_schema_has_command_required(tmp_path):
    from flux.tasks.ai.tool_executor import build_tool_schemas

    tools = system_tools(workspace=tmp_path)
    schemas = build_tool_schemas(tools)
    shell_schema = next(s for s in schemas if s["name"] == "shell")
    assert "command" in shell_schema["parameters"]["properties"]
    assert "command" in shell_schema["parameters"]["required"]
    assert "stream" in shell_schema["parameters"]["properties"]
    assert "stream" not in shell_schema["parameters"]["required"]


def test_read_file_schema_has_path_required(tmp_path):
    from flux.tasks.ai.tool_executor import build_tool_schemas

    tools = system_tools(workspace=tmp_path)
    schemas = build_tool_schemas(tools)
    schema = next(s for s in schemas if s["name"] == "read_file")
    assert "path" in schema["parameters"]["required"]
    assert "offset" not in schema["parameters"]["required"]
    assert "limit" not in schema["parameters"]["required"]
