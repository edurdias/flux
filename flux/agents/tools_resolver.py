from __future__ import annotations

from pathlib import Path
from typing import Any

from flux.task import task


def resolve_builtin_tools(tools_config: list[Any]) -> list[task]:
    resolved = []

    for tool_entry in tools_config:
        if isinstance(tool_entry, str):
            resolved.extend(_resolve_by_name(tool_entry))
        elif isinstance(tool_entry, dict):
            for key, config in tool_entry.items():
                resolved.extend(_resolve_tool_group(key, config or {}))
        else:
            raise ValueError(f"Unknown tool entry format: {tool_entry}")

    return resolved


def _resolve_tool_group(name: str, config: dict) -> list[task]:
    from flux.tasks.ai.tools.system_tools import DEFAULT_BLOCKLIST, SystemToolsConfig

    workspace = Path(config.get("workspace", ".")).resolve()
    timeout = config.get("timeout", 30)
    max_output_chars = config.get("max_output_chars", 100_000)
    blocklist = config.get("blocklist", None)

    sys_config = SystemToolsConfig(
        workspace=workspace,
        timeout=timeout,
        blocklist=blocklist if blocklist is not None else list(DEFAULT_BLOCKLIST),
        max_output_chars=max_output_chars,
    )

    if name == "system_tools":
        from flux.tasks.ai.tools.directory import build_directory_tools
        from flux.tasks.ai.tools.files import build_file_tools
        from flux.tasks.ai.tools.search import build_search_tools
        from flux.tasks.ai.tools.shell import build_shell_tools

        return [
            *build_shell_tools(sys_config),
            *build_file_tools(sys_config),
            *build_search_tools(sys_config),
            *build_directory_tools(sys_config),
        ]
    elif name == "shell":
        from flux.tasks.ai.tools.shell import build_shell_tools

        return list(build_shell_tools(sys_config))
    elif name == "files":
        from flux.tasks.ai.tools.files import build_file_tools

        return list(build_file_tools(sys_config))
    elif name == "search":
        from flux.tasks.ai.tools.search import build_search_tools

        return list(build_search_tools(sys_config))
    elif name == "directory":
        from flux.tasks.ai.tools.directory import build_directory_tools

        return list(build_directory_tools(sys_config))
    else:
        raise ValueError(f"Unknown tool group: '{name}'")


def _resolve_by_name(name: str) -> list[task]:
    try:
        return _resolve_tool_group(name, {})
    except ValueError:
        raise ValueError(
            f"Unknown tool: '{name}'. Available: system_tools, shell, files, search, directory"
        )
