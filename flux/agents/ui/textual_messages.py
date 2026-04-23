"""Custom Textual messages for the agent TUI bridge."""

from __future__ import annotations

import asyncio
from typing import Any

from textual.message import Message


class TokenReceived(Message):
    def __init__(self, text: str) -> None:
        super().__init__()
        self.text = text


class ToolStarted(Message):
    def __init__(self, name: str, args: dict[str, Any]) -> None:
        super().__init__()
        self.name = name
        self.args = args


class ToolCompleted(Message):
    def __init__(self, name: str, status: str) -> None:
        super().__init__()
        self.name = name
        self.status = status


class ReasoningReceived(Message):
    def __init__(self, text: str) -> None:
        super().__init__()
        self.text = text


class ResponseReceived(Message):
    def __init__(self, content: str | None) -> None:
        super().__init__()
        self.content = content


class ReplyStarted(Message):
    pass


class ReplyEnded(Message):
    pass


class SessionInfoReceived(Message):
    def __init__(self, session_id: str, agent_name: str) -> None:
        super().__init__()
        self.session_id = session_id
        self.agent_name = agent_name


class SessionEnded(Message):
    def __init__(self, reason: str, turns: int) -> None:
        super().__init__()
        self.reason = reason
        self.turns = turns


class ElicitationRequested(Message):
    def __init__(self, request: dict[str, Any], future: asyncio.Future) -> None:
        super().__init__()
        self.request = request
        self.future = future
