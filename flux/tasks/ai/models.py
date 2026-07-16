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


class Usage(BaseModel):
    """Token usage reported by a provider for a single LLM call."""

    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class LLMResponse(BaseModel):
    text: str = ""
    tool_calls: list[ToolCall] = []
    reasoning: ReasoningContent | None = None
    # Populated by providers that report token usage; None when the provider
    # (or a custom formatter) does not expose it.
    usage: Usage | None = None
