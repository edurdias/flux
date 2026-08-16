"""Selector grammar for outbound hooks: parse, validate, and match.

A selector is a `:`-delimited string naming an engine event. It reuses the
same wildcard semantics as the permission matcher
(`flux.security.identity._wildcard_match`) rather than growing a second
implementation:

    execution:<namespace>:<workflow_name>:<state>
    task:<namespace>:<workflow_name>:<task_name>:<event_type>

`*` in the last segment matches any number of remaining segments; a `*`
elsewhere matches exactly one segment.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from flux.domain.events import ExecutionEvent, ExecutionEventType
from flux.domain.execution_context import ExecutionContext
from flux.security.identity import _wildcard_match

DOMAINS = ("execution", "task")  # segment counts: 4 and 5

# Full segment count for each domain, e.g. execution:<ns>:<wf>:<state> == 4.
_DOMAIN_WIDTHS = {"execution": 4, "task": 5}

# The state each workflow event announces. Spelled out rather than derived by
# stripping the `WORKFLOW_` prefix because two entries do not match their own
# name -- both STARTED and RESUMED land the execution in `running` -- and
# because a checkpoint carries a *delta* of events, so the key has to come
# from the event rather than from wherever the execution has since arrived:
# a delta of STARTED + COMPLETED is two transitions, not two completions.
_WORKFLOW_EVENT_STATES = {
    "WORKFLOW_SCHEDULED": "scheduled",
    "WORKFLOW_CLAIMED": "claimed",
    "WORKFLOW_STARTED": "running",
    "WORKFLOW_COMPLETED": "completed",
    "WORKFLOW_FAILED": "failed",
    "WORKFLOW_PAUSED": "paused",
    "WORKFLOW_RESUMING": "resuming",
    "WORKFLOW_RESUMED": "running",
    "WORKFLOW_RESUME_SCHEDULED": "resume_scheduled",
    "WORKFLOW_RESUME_CLAIMED": "resume_claimed",
    "WORKFLOW_CANCELLING": "cancelling",
    "WORKFLOW_CANCELLED": "cancelled",
}


def validate_selector(selector: str) -> None:
    """Raise ``ValueError`` naming the problem if ``selector`` is malformed.

    Used by the routes' 400 path, so the message is meant to be surfaced
    to the caller as-is.
    """
    if not selector:
        raise ValueError("selector must not be empty")

    parts = selector.split(":")
    if len(parts) < 2:
        raise ValueError(f"selector {selector!r} must have a domain and at least one segment")

    domain = parts[0]
    if domain not in _DOMAIN_WIDTHS:
        raise ValueError(
            f"selector {selector!r} has unknown domain {domain!r}; expected one of {DOMAINS}",
        )

    width = _DOMAIN_WIDTHS[domain]

    # A terminal `*` legitimately covers the remainder, so it is exempt from
    # the exact segment-count check (`execution:*` and `task:*` are both
    # valid, deliberately under-width selectors) -- but it covers the
    # remainder of a key, it does not extend one. The segments before it
    # still have to line up with a real event key, so a prefix longer than
    # the domain ever emits matches nothing, whatever follows it. That is
    # worse than a malformed selector: the hook is accepted and then simply
    # never fires.
    if parts[-1] == "*":
        if len(parts) - 1 > width:
            raise ValueError(
                f"selector {selector!r} has {len(parts) - 1} segments before its "
                f"trailing '*', more than the {width} a {domain!r} event key has; "
                "it could never match",
            )
        return

    if len(parts) != width:
        raise ValueError(
            f"selector {selector!r} has {len(parts)} segments, expected {width} for domain {domain!r}",
        )


def selector_matches(selector: str, event_key: str) -> bool:
    """Whether ``event_key`` (a fully-qualified event key) matches ``selector``."""
    return _wildcard_match(selector.split(":"), event_key.split(":"))


@dataclass(frozen=True)
class HookEvent:
    domain: str  # "execution" | "task"
    key: str  # the matchable string, e.g. "task:release:promote:promote_prod:awaiting_approval"
    execution_id: str
    workflow_namespace: str
    workflow_name: str
    event_id: str  # the persisted ExecutionEvent id
    type: str  # lower-cased state or event-type value
    task_name: str | None
    task_call_id: str | None
    value: Any
    occurred_at: str  # ISO-8601
    # Every agent session runs agents/agent_chat, so a selector's own text
    # cannot discriminate between agents -- an agent-owned hook's runtime
    # backstop (HookRegistry.matches) compares this against the hook's
    # owner_ref instead. None outside the "agents" namespace.
    agent: str | None = None

    @property
    def delivery_key(self) -> str:
        """This event's identity for a delivery, scoped to its execution.

        ``event_id`` is a hash over (name, type, source_id, value, time) and
        carries nothing execution-specific: two executions scheduled to the
        same worker in the same clock tick share one. The engine's own event
        dedup scopes the id by ``execution_id`` for that reason, and the
        deliveries' ``(hook_id, event_key)`` constraint has to do the same --
        unscoped, the second execution's delivery is silently swallowed as a
        duplicate and its event is missed.
        """
        return f"{self.execution_id}:{self.event_id}"


def events_from_save(
    ctx: ExecutionContext,
    new_events: Sequence[ExecutionEvent],
) -> list[HookEvent]:
    """Derive the ``HookEvent``s a save is about to persist.

    One per event: `WORKFLOW_*` rows produce the `execution` domain keyed on
    the state that event announces; `TASK_*` rows produce the `task` domain
    keyed on the event type with its `TASK_` prefix stripped. Any other
    event type -- and any workflow event with no state of its own -- is
    skipped rather than raising: it should not occur, but a hook derivation
    is not the place to fail a save over it.
    """
    # The same derivation flux/api/workflow_routes.py uses to record
    # AgentSessionModel at execution creation -- ctx.input is set once at
    # ExecutionContext construction and never overwritten by a resume, so
    # this reads the same "agent" key throughout the execution's life.
    agent = (
        ctx.input.get("agent")
        if ctx.workflow_namespace == "agents" and isinstance(ctx.input, dict)
        else None
    )

    produced: list[HookEvent] = []
    for event in new_events:
        event_type = event.type
        # Wire-rebuilt events can carry the type as a plain str; both forms
        # stringify to the same "WORKFLOW_*"/"TASK_*" token.
        type_name = (
            event_type.value if isinstance(event_type, ExecutionEventType) else str(event_type)
        )

        if type_name.startswith("WORKFLOW_"):
            state = _WORKFLOW_EVENT_STATES.get(type_name)
            if state is None:
                continue
            produced.append(
                HookEvent(
                    domain="execution",
                    key=f"execution:{ctx.workflow_namespace}:{ctx.workflow_name}:{state}",
                    execution_id=ctx.execution_id,
                    workflow_namespace=ctx.workflow_namespace,
                    workflow_name=ctx.workflow_name,
                    event_id=event.id,
                    type=state,
                    task_name=None,
                    task_call_id=None,
                    value=event.value,
                    occurred_at=event.time.isoformat(),
                    agent=agent,
                ),
            )
        elif type_name.startswith("TASK_"):
            task_type = type_name.removeprefix("TASK_").lower()
            produced.append(
                HookEvent(
                    domain="task",
                    key=f"task:{ctx.workflow_namespace}:{ctx.workflow_name}:{event.name}:{task_type}",
                    execution_id=ctx.execution_id,
                    workflow_namespace=ctx.workflow_namespace,
                    workflow_name=ctx.workflow_name,
                    event_id=event.id,
                    type=task_type,
                    task_name=event.name,
                    task_call_id=event.source_id,
                    value=event.value,
                    occurred_at=event.time.isoformat(),
                    agent=agent,
                ),
            )
        # else: not a hook-domain event type -- skip rather than raise.

    return produced
