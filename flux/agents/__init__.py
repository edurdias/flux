from __future__ import annotations

from typing import TYPE_CHECKING

# AgentManager (via flux.agents.manager) drags in flux.models -> sqlalchemy;
# AgentProcess pulls in the UI stack. Keeping both behind __getattr__ lets a
# lean console process do `import flux.agents.ui.api` without paying for
# either (issue: agent-console Task 2).
if TYPE_CHECKING:
    from flux.agents.manager import AgentManager  # noqa: F401
    from flux.agents.process import AgentProcess  # noqa: F401
    from flux.agents.types import AgentDefinition  # noqa: F401
    from flux.agents.types import ChatResponseOutput  # noqa: F401
    from flux.agents.types import SessionEndOutput  # noqa: F401

__all__ = [
    "AgentDefinition",
    "AgentManager",
    "AgentProcess",
    "ChatResponseOutput",
    "SessionEndOutput",
]


def __getattr__(name: str):
    if name == "AgentManager":
        from flux.agents.manager import AgentManager

        return AgentManager
    if name == "AgentProcess":
        from flux.agents.process import AgentProcess

        return AgentProcess
    if name in ("AgentDefinition", "ChatResponseOutput", "SessionEndOutput"):
        from flux.agents import types

        return getattr(types, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(list(globals()) + __all__)
