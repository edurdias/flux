from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from flux.task import task

DEFAULT_BLOCKLIST = [
    r"\brm\s+-rf\s+/",
    r"\bmkfs\b",
    r"\bdd\s",
    r"\bshutdown\b",
    r"\breboot\b",
    r"\b:\(\)\s*\{",
    r"\b>\s*/dev/sd",
]


@dataclass
class SystemToolsConfig:
    workspace: Path
    timeout: int
    blocklist: list[str]
    max_output_chars: int


def resolve_path(config: SystemToolsConfig, path: str) -> Path:
    workspace = config.workspace.resolve()
    if Path(path).is_absolute():
        resolved = Path(path).resolve()
    else:
        resolved = (workspace / path).resolve()
    if not resolved.is_relative_to(workspace):
        raise ValueError(f"path escapes workspace boundary: {path}")
    return resolved


def truncate_output(text: str, max_chars: int) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars], True


def system_tools(
    workspace: str | Path,
    timeout: int = 30,
    blocklist: list[str] | None = None,
    max_output_chars: int = 100_000,
) -> list[task]:
    config = SystemToolsConfig(
        workspace=Path(workspace).resolve(),
        timeout=timeout,
        blocklist=blocklist if blocklist is not None else list(DEFAULT_BLOCKLIST),
        max_output_chars=max_output_chars,
    )

    from flux.tasks.ai.tools.shell import build_shell_tools
    from flux.tasks.ai.tools.files import build_file_tools
    from flux.tasks.ai.tools.search import build_search_tools
    from flux.tasks.ai.tools.directory import build_directory_tools

    return [
        *build_shell_tools(config),
        *build_file_tools(config),
        *build_search_tools(config),
        *build_directory_tools(config),
    ]
