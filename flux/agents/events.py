from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

# Raw wire-format discriminators from Flux SSE.
#
# Progress events:   data = {"type": "TASK_PROGRESS", "value": {<progress value>}, ...}
# State events:      data = {"execution_id": ..., "state": "PAUSED"|..., "output": {...}}
#
# The `state` field is the ExecutionState enum value (see flux/domain/events.py).
WIRE_TASK_PROGRESS = "TASK_PROGRESS"

STATE_PAUSED = "PAUSED"
STATE_COMPLETED = "COMPLETED"
STATE_FAILED = "FAILED"
STATE_CANCELLED = "CANCELLED"
TERMINAL_STATES = frozenset({STATE_PAUSED, STATE_COMPLETED, STATE_FAILED, STATE_CANCELLED})

# Parsed event kinds (downstream contract — do not rename; UIs depend on these strings).
KIND_SESSION_ID = "session_id"
KIND_TOKEN = "token"
KIND_TOOL_START = "tool_start"
KIND_TOOL_DONE = "tool_done"
KIND_CHAT_RESPONSE = "chat_response"
KIND_ELICITATION = "elicitation"
KIND_SESSION_END = "session_end"
KIND_REASONING = "reasoning"


@dataclass(frozen=True)
class AgentEvent:
    kind: str
    data: dict[str, Any]


def parse_event(raw: dict[str, Any]) -> Iterable[AgentEvent]:
    """Parse a raw SSE data frame into zero or more AgentEvents.

    Accepts two wire formats produced by Flux's streaming endpoints:

    * Progress frames — ``{"type": "TASK_PROGRESS", "value": {...}, ...}``.
      ``value`` is the dict passed to ``progress()`` inside a task; the agent
      loop emits ``{"token": ...}``, ``{"type": "tool_start", ...}``, or
      ``{"type": "tool_done", ...}``.

    * State frames (``ExecutionContextDTO.summary()``) —
      ``{"execution_id": ..., "state": <ExecutionState>, "output": {...}, ...}``.
      When the workflow pauses, ``output`` is the pause payload (chat_response,
      elicitation, or session_end).

    A single state frame carries both an ``execution_id`` and a ``state``, so
    we may yield multiple events from one frame. Unknown shapes yield nothing.
    """
    if "execution_id" in raw:
        yield AgentEvent(kind=KIND_SESSION_ID, data={"id": raw["execution_id"]})

    wire_type = raw.get("type", "")

    if wire_type == WIRE_TASK_PROGRESS:
        value = raw.get("value") or {}
        if not isinstance(value, dict):
            return
        if "token" in value:
            yield AgentEvent(kind=KIND_TOKEN, data={"text": value["token"]})
        elif value.get("type") == KIND_TOOL_START:
            yield AgentEvent(
                kind=KIND_TOOL_START,
                data={
                    "id": value.get("id", ""),
                    "name": value.get("name", ""),
                    "args": value.get("args", {}),
                },
            )
        elif value.get("type") == KIND_TOOL_DONE:
            yield AgentEvent(
                kind=KIND_TOOL_DONE,
                data={
                    "id": value.get("id", ""),
                    "name": value.get("name", ""),
                    "status": value.get("status", ""),
                },
            )
        elif value.get("type") == KIND_REASONING:
            yield AgentEvent(
                kind=KIND_REASONING,
                data={"text": value.get("text", "")},
            )
        return

    # State frame — ExecutionContextDTO.summary(). We only surface semantics
    # for PAUSED (the interesting case for chat). COMPLETED/FAILED/CANCELLED
    # end the stream but do not produce a distinct AgentEvent kind yet.
    state = raw.get("state")
    if isinstance(state, str) and state.upper() == STATE_PAUSED:
        output = raw.get("output") or {}
        if not isinstance(output, dict):
            return
        pause_type = output.get("type", "")
        if pause_type == KIND_CHAT_RESPONSE:
            yield AgentEvent(
                kind=KIND_CHAT_RESPONSE,
                data={
                    "content": output.get("content"),
                    "turn": output.get("turn", 0),
                },
            )
        elif pause_type == KIND_ELICITATION:
            yield AgentEvent(
                kind=KIND_ELICITATION,
                data={
                    "elicitation_id": output.get("elicitation_id", ""),
                    "url": output.get("url", ""),
                    "message": output.get("message", ""),
                    "server_name": output.get("server_name", ""),
                    "mode": output.get("mode", "url"),
                },
            )
        elif pause_type == KIND_SESSION_END:
            yield AgentEvent(
                kind=KIND_SESSION_END,
                data={
                    "reason": output.get("reason", ""),
                    "turns": output.get("turns", 0),
                },
            )


def is_terminal_state(raw: dict[str, Any]) -> bool:
    """Return True if a state frame indicates the stream batch is complete.

    PAUSED means the workflow is waiting for the next user action — from the
    HTTP client's perspective this is just as terminal as COMPLETED/FAILED/
    CANCELLED: there will be no further events on this SSE until a separate
    resume call.
    """
    state = raw.get("state")
    return isinstance(state, str) and state.upper() in TERMINAL_STATES
