from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class ModelCapabilities(BaseModel):
    """What a model can do, as far as Flux needs to know (issue #141).

    Resolved by ``flux.tasks.ai.capabilities.resolve_capabilities`` — a
    curated matrix keyed by the full ``provider/model`` string, with
    conservative heuristics for anything not listed. No network involved.

    The agent runtime consumes ``tools`` (clear Flux error instead of a
    provider 400 when tools are passed to a model that cannot call them),
    ``parallel_tool_calls`` (a turn's tool calls run strictly in emission
    order when False — many local models fake parallel calls), and
    ``streaming`` (token streaming is skipped where known broken).
    ``vision``/``pdf`` are carried as data for callers to query; the agent
    input surface is text-only today, so nothing gates on them yet.
    """

    model_config = ConfigDict(frozen=True)

    tools: bool = True
    vision: bool = False
    pdf: bool = False
    parallel_tool_calls: bool = False
    streaming: bool = True
    # Display label for UIs; None means "use the model string".
    label: str | None = None


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
