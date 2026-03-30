from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from flux.task import task
    from flux.tasks.ai.tools.system_tools import SystemToolsConfig


def build_directory_tools(config: SystemToolsConfig) -> list[task]:
    return []
