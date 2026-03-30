from __future__ import annotations

import inspect
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
    """Wraps every delegation response with a uniform status envelope."""

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
    """Internal result type returned by workflow agents before wrapping into DelegationResult."""

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
    """Build a system-prompt section listing available agents and delegation semantics."""
    lines = [
        "\n\n## Sub-Agents",
        "",
        "You have sub-agents available. To delegate work, call the `delegate` "
        "tool — do not describe or simulate delegation in text.",
        "",
        "Available agents:",
    ]
    for a in agents:
        lines.append(f"- {a.name}: {a.description}")

    lines.extend(
        [
            "",
            "Call `delegate` with:",
            "- agent (required): agent name from the list above",
            "- instruction (required): clear description of the task. "
            "Sub-agents cannot see your conversation, so include any "
            "relevant context or data they need in the instruction",
            "- input: additional data the agent needs (JSON string or plain text)",
            "- expected_output: format you want back",
            "",
            "The tool returns a JSON object with status "
            '("completed", "paused", or "failed") and the agent\'s output.',
            "",
            "Each agent starts with a blank context. When chaining agents, "
            "consider passing relevant output from previous delegations "
            "so the next agent has the context it needs.",
        ],
    )
    return "\n".join(lines)


def build_delegate(agents: list) -> task:
    """Create a ``delegate`` @task that dispatches to agents by name.

    Validates and indexes agents at construction time so dispatch is a
    simple dict lookup at call time.
    """
    registry: dict[str, Callable] = {}
    for a in agents:
        _validate_agent(a)
        if a.name in registry:
            raise AgentValidationError(f"Duplicate agent name: '{a.name}'")
        registry[a.name] = a

    @task
    async def delegate(
        agent: str,
        instruction: str = "",
        input: str | None = None,
        expected_output: str | None = None,
        execution_id: str | None = None,
    ) -> dict:
        """Delegate a task to a specialized agent.

        Args:
            agent: Name of the agent to delegate to.
            instruction: Natural language description of what to do.
            input: Data the agent needs (JSON string or plain text).
            expected_output: Description of the desired response format.
            execution_id: Resume a previously paused agent. Pass the
                          execution_id from the paused response.

        Returns:
            Dict with: agent, status, output, and optionally execution_id.
        """
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

        context = ""
        if parsed_input is not None:
            context = (
                json.dumps(parsed_input) if not isinstance(parsed_input, str) else parsed_input
            )

        try:
            kwargs: dict[str, Any] = {}
            if context:
                kwargs["context"] = context
            if execution_id is not None:
                target_func = target.func if hasattr(target, "func") else target
                sig = inspect.signature(target_func)
                accepts_execution_id = "execution_id" in sig.parameters or any(
                    p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
                )
                if accepts_execution_id:
                    kwargs["execution_id"] = execution_id

            raw = await target(full_instruction, **kwargs)

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
    """Create an agent backed by a remote Flux workflow.

    The returned task uses FluxClient to call the workflow synchronously
    and returns a WorkflowAgentResult with status, output, and execution_id.
    """
    _validate_agent_name(name)

    @task.with_options(name=name)
    async def _workflow_task(
        instruction: str,
        *,
        context: str = "",
        execution_id: str | None = None,
    ) -> WorkflowAgentResult:
        async with _get_client() as client:
            input_value: Any = _parse_input(context or None)
            payload = {"instruction": instruction, "input": input_value}
            if execution_id:
                response = await client.resume_execution_sync(
                    workflow,
                    execution_id,
                    payload,
                )
            else:
                response = await client.run_workflow_sync(
                    workflow,
                    payload,
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
    return FluxClient(
        config.settings.workers.server_url,
        timeout=config.settings.workers.default_timeout or None,
    )


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
