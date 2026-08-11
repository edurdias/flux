from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

from flux.runners.subprocess_runner import SANDBOX_ENV_VAR as SANDBOX_ENV_VAR
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


def in_sandbox() -> bool:
    return os.environ.get(SANDBOX_ENV_VAR) == "1"


def require_sandbox_for_shell(allow_unsandboxed: bool = False) -> None:
    """Raise unless shell may be built here.

    Both system_tools() and the YAML tool-group resolver build shell, so the
    check lives where each can reach it.
    """
    if allow_unsandboxed or in_sandbox():
        return
    raise ValueError(
        "The shell tool runs commands as the worker user unless the execution "
        "is containerized. Pin the workflow to a container runner "
        '(@workflow.with_options(runner="docker")), or set '
        "allow_unsandboxed_shell to accept that.",
    )


def resolve_path(config: SystemToolsConfig, path: str) -> Path:
    workspace = config.workspace.resolve()
    if Path(path).is_absolute():
        resolved = Path(path).resolve()
    else:
        resolved = (workspace / path).resolve()
    if not resolved.is_relative_to(workspace):
        raise ValueError(f"path escapes workspace boundary: {path}")
    return resolved


def is_contained(config: SystemToolsConfig, path: Path | str) -> bool:
    """Whether ``path`` resolves inside the workspace.

    Tools that walk the tree need this per entry, not just per root:
    ``resolve_path`` validates the path it is handed, but a symlink *inside*
    the workspace resolves outside it, so a walk reaches files the boundary is
    meant to exclude.
    """
    try:
        return Path(path).resolve().is_relative_to(config.workspace.resolve())
    except OSError:
        return False


def truncate_output(text: str, max_chars: int) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars], True


def system_tools(
    workspace: str | Path,
    timeout: int = 30,
    blocklist: list[str] | None = None,
    max_output_chars: int = 100_000,
    *,
    include_shell: bool = True,
    allow_unsandboxed_shell: bool = False,
) -> list[task]:
    """Build the system tools for an agent.

    ``shell`` requires a container runner unless ``allow_unsandboxed_shell``
    says otherwise: outside one it runs as the worker user, with that user's
    ssh keys, cloud credentials and flux.toml.
    """
    if include_shell:
        require_sandbox_for_shell(allow_unsandboxed_shell)

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
        *(build_shell_tools(config) if include_shell else []),
        *build_file_tools(config),
        *build_search_tools(config),
        *build_directory_tools(config),
    ]
