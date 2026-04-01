from __future__ import annotations

from pydantic import BaseModel


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict


class LLMResponse(BaseModel):
    text: str = ""
    tool_calls: list[ToolCall] = []
