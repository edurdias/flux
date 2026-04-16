from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AgentEvent:
    kind: str
    data: dict


def parse_event(raw: dict[str, Any]) -> Iterable[AgentEvent]:
    """Parse a raw SSE event payload into zero or more AgentEvents.

    A single SSE frame may carry both an execution_id handshake and a progress
    value, so we yield multiple events.
    """
    if "execution_id" in raw:
        yield AgentEvent(kind="session_id", data={"id": raw["execution_id"]})

    event_type = raw.get("type", "")

    if event_type == "task.progress":
        value = raw.get("value") or {}
        if "token" in value:
            yield AgentEvent(kind="token", data={"text": value["token"]})
        elif value.get("type") == "tool_start":
            yield AgentEvent(
                kind="tool_start",
                data={"name": value.get("name", ""), "args": value.get("args", {})},
            )
        elif value.get("type") == "tool_done":
            yield AgentEvent(
                kind="tool_done",
                data={"name": value.get("name", ""), "status": value.get("status", "")},
            )
        return

    if "paused" in event_type:
        output = raw.get("output") or {}
        pause_type = output.get("type", "")
        if pause_type == "chat_response":
            yield AgentEvent(
                kind="chat_response",
                data={
                    "content": output.get("content"),
                    "turn": output.get("turn", 0),
                },
            )
        elif pause_type == "elicitation":
            yield AgentEvent(
                kind="elicitation",
                data={
                    "elicitation_id": output.get("elicitation_id", ""),
                    "url": output.get("url", ""),
                    "message": output.get("message", ""),
                    "server_name": output.get("server_name", ""),
                    "mode": output.get("mode", "url"),
                },
            )
        elif pause_type == "session_end":
            yield AgentEvent(
                kind="session_end",
                data={
                    "reason": output.get("reason", ""),
                    "turns": output.get("turns", 0),
                },
            )
