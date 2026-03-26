from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass
from typing import Any, Literal

from flux.errors import ExecutionError

logger = logging.getLogger("flux.delegation")

_NAME_PATTERN = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")


class AgentNotFoundError(ExecutionError):
    """Raised when delegate is called with an unknown agent name."""

    def __init__(self, message: str):
        super().__init__(message=message)


class AgentValidationError(ValueError):
    pass


@dataclass
class DelegationResult:
    agent: str
    status: Literal["completed", "paused", "failed"]
    output: Any
    execution_id: str | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        if d["execution_id"] is None:
            del d["execution_id"]
        return d


@dataclass
class WorkflowAgentResult:
    status: Literal["completed", "paused", "failed"]
    output: Any
    execution_id: str


def _validate_agent_name(name: str) -> None:
    if not name:
        raise AgentValidationError("Agent name must not be empty.")
    if len(name) > 64:
        raise AgentValidationError(f"Agent name '{name}' exceeds 64 characters.")
    if "--" in name:
        raise AgentValidationError(
            f"Agent name '{name}' must not contain consecutive hyphens."
        )
    if not _NAME_PATTERN.match(name):
        raise AgentValidationError(
            f"Agent name '{name}' is invalid. Use lowercase letters, numbers, "
            f"and single hyphens. Must not start or end with a hyphen."
        )


def _validate_agent(agent) -> None:
    if not callable(agent):
        raise AgentValidationError(f"Sub-agent must be callable. Got: {type(agent)}")
    if not hasattr(agent, "name") or not agent.name:
        raise AgentValidationError("Sub-agent must have a name attribute.")
    if not hasattr(agent, "description") or not agent.description:
        raise AgentValidationError(
            f"Sub-agent '{getattr(agent, 'name', '?')}' must have a "
            f"non-empty description attribute."
        )
    _validate_agent_name(agent.name)


def _parse_input(input: str | None) -> Any:
    if input is None:
        return None
    try:
        return json.loads(input)
    except (json.JSONDecodeError, TypeError):
        return input
