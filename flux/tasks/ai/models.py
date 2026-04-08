from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict


class ReasoningContent(BaseModel):
    text: str | None = None
    opaque: Any = None


class LLMResponse(BaseModel):
    text: str = ""
    tool_calls: list[ToolCall] = []
    reasoning: ReasoningContent | None = None
