from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass
from typing import Any, Callable, Literal

from flux.errors import ExecutionError, PauseRequested
from flux.task import task

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
        raise AgentValidationError(f"Agent name '{name}' must not contain consecutive hyphens.")
    if not _NAME_PATTERN.match(name):
        raise AgentValidationError(
            f"Agent name '{name}' is invalid. Use lowercase letters, numbers, "
            f"and single hyphens. Must not start or end with a hyphen.",
        )


def _validate_agent(agent) -> None:
    if not callable(agent):
        raise AgentValidationError(f"Sub-agent must be callable. Got: {type(agent)}")
    if not hasattr(agent, "name") or not agent.name:
        raise AgentValidationError("Sub-agent must have a name attribute.")
    if not hasattr(agent, "description") or not agent.description:
        raise AgentValidationError(
            f"Sub-agent '{getattr(agent, 'name', '?')}' must have a "
            f"non-empty description attribute.",
        )
    _validate_agent_name(agent.name)


def _parse_input(input: str | None) -> Any:
    if input is None:
        return None
    try:
        return json.loads(input)
    except (json.JSONDecodeError, TypeError):
        return input


def build_agents_preamble(agents: list) -> str:
    lines = [
        "\n\nYou can delegate tasks to specialized agents using the delegate tool.",
        "",
        "Available agents:",
    ]
    for a in agents:
        lines.append(f"- {a.name}: {a.description}")

    lines.extend(
        [
            "",
            "When delegating:",
            "- Provide clear instructions describing what you need done",
            "- Include relevant input data the agent needs",
            "- Describe the expected output format so the agent knows what to return",
            "",
            "Delegation responses have a status field:",
            "- completed: task is done, output contains the result",
            "- paused: the agent needs more information. Read the output to "
            "understand what is needed. Call delegate again with the same agent "
            "name and execution_id, providing what was asked for",
            "- failed: the agent encountered an error",
        ],
    )
    return "\n".join(lines)


def build_delegate(agents: list) -> task:
    registry: dict[str, Callable] = {}
    for a in agents:
        _validate_agent(a)
        if a.name in registry:
            raise AgentValidationError(f"Duplicate agent name: '{a.name}'")
        registry[a.name] = a

    @task
    async def delegate(
        agent: str,
        instruction: str,
        input: str | None = None,
        expected_output: str | None = None,
        execution_id: str | None = None,
    ) -> dict:
        target = registry.get(agent)
        if target is None:
            available = ", ".join(registry.keys())
            return DelegationResult(
                agent=agent,
                status="failed",
                output=f"Agent '{agent}' not found. Available: {available}",
            ).to_dict()

        parsed_input = _parse_input(input)

        full_instruction = instruction
        if expected_output is not None:
            full_instruction += f"\n\nExpected output format: {expected_output}"

        try:
            if execution_id is not None:
                raw = await target(
                    full_instruction,
                    input=parsed_input,
                    execution_id=execution_id,
                )
            else:
                raw = await target(
                    full_instruction,
                    input=parsed_input,
                )

            if isinstance(raw, WorkflowAgentResult):
                result = DelegationResult(
                    agent=agent,
                    status=raw.status,
                    output=raw.output,
                    execution_id=raw.execution_id,
                )
            else:
                result = DelegationResult(
                    agent=agent,
                    status="completed",
                    output=raw,
                )

        except PauseRequested as e:
            result = DelegationResult(
                agent=agent,
                status="paused",
                output=e.output,
            )

        except Exception as e:
            result = DelegationResult(
                agent=agent,
                status="failed",
                output=str(e),
            )

        return result.to_dict()

    return delegate


def workflow_agent(
    name: str,
    description: str,
    workflow: str,
) -> task:
    _validate_agent_name(name)

    @task.with_options(name=name)
    async def _workflow_task(
        instruction: str,
        *,
        input: Any | None = None,
        execution_id: str | None = None,
    ) -> WorkflowAgentResult:
        client = _get_client()

        if execution_id:
            response = await client.resume_execution_sync(
                workflow,
                execution_id,
                {"instruction": instruction, "input": input},
            )
        else:
            response = await client.run_workflow_sync(
                workflow,
                {"instruction": instruction, "input": input},
            )

        return WorkflowAgentResult(
            status=_map_execution_state(response),
            output=response.get("output"),
            execution_id=response.get("execution_id"),
        )

    _workflow_task.description = description
    return _workflow_task


def _get_client():
    from flux.client import FluxClient
    from flux.config import Configuration

    config = Configuration.get()
    return FluxClient(config.settings.workers.server_url, timeout=None)


def _map_execution_state(response: dict) -> Literal["completed", "paused", "failed"]:
    state = response.get("state", "").upper()
    if state == "COMPLETED":
        return "completed"
    elif state == "PAUSED":
        return "paused"
    else:
        if state not in ("FAILED",):
            logger.warning("Unexpected execution state from server: %s", state)
        return "failed"
