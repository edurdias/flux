from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

# Raw frame types (wire format from Flux SSE)
RAW_TASK_PROGRESS = "task.progress"
RAW_EXECUTION_PAUSED = "execution.paused"

# Parsed event kinds (downstream contract)
KIND_SESSION_ID = "session_id"
KIND_TOKEN = "token"
KIND_TOOL_START = "tool_start"
KIND_TOOL_DONE = "tool_done"
KIND_CHAT_RESPONSE = "chat_response"
KIND_ELICITATION = "elicitation"
KIND_SESSION_END = "session_end"


@dataclass(frozen=True)
class AgentEvent:
    kind: str
    data: dict[str, Any]


def parse_event(raw: dict[str, Any]) -> Iterable[AgentEvent]:
    """Parse a raw SSE event payload into zero or more AgentEvents.

    A single SSE frame may carry both an execution_id handshake and a progress
    value, so we yield multiple events.

    Unknown event types yield nothing (silent skip).
    """
    if "execution_id" in raw:
        yield AgentEvent(kind=KIND_SESSION_ID, data={"id": raw["execution_id"]})

    event_type = raw.get("type", "")

    if event_type == RAW_TASK_PROGRESS:
        value = raw.get("value") or {}
        if "token" in value:
            yield AgentEvent(kind=KIND_TOKEN, data={"text": value["token"]})
        elif value.get("type") == KIND_TOOL_START:
            yield AgentEvent(
                kind=KIND_TOOL_START,
                data={"name": value.get("name", ""), "args": value.get("args", {})},
            )
        elif value.get("type") == KIND_TOOL_DONE:
            yield AgentEvent(
                kind=KIND_TOOL_DONE,
                data={"name": value.get("name", ""), "status": value.get("status", "")},
            )
        return

    if event_type == RAW_EXECUTION_PAUSED:
        output = raw.get("output") or {}
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
