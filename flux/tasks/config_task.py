from __future__ import annotations

from typing import Any

from flux.task import task


@task.with_options(
    name="get_config_{key}",
    config_requests=["{key}"],
)
async def get_config(key: str, config: dict | None = None) -> Any:
    if config is None or key not in config:
        raise ValueError(f"Config '{key}' not found")
    return config[key]
