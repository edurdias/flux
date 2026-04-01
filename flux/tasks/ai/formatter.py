from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any

from flux.tasks.ai.models import LLMResponse, ToolCall


class LLMFormatter(ABC):
    @abstractmethod
    def build_messages(
        self,
        system_prompt: str,
        user_content: str,
        working_memory: Any | None = None,
    ) -> tuple[list[Any], dict]:
        """Build initial message list and provider-specific call kwargs."""

    @abstractmethod
    def format_assistant_message(self, response: LLMResponse) -> Any:
        """Convert LLMResponse into a message to append to conversation."""

    @abstractmethod
    def format_tool_results(
        self,
        tool_calls: list[ToolCall],
        results: list[dict],
    ) -> list[Any]:
        """Format tool execution results as messages for next turn."""

    @abstractmethod
    def format_user_message(self, text: str) -> Any:
        """Format a plain user message."""

    @abstractmethod
    def remove_tools_from_kwargs(self, call_kwargs: dict) -> dict:
        """Return call_kwargs without tools."""

    @abstractmethod
    async def stream(
        self,
        messages: list[Any],
        call_kwargs: dict,
    ) -> AsyncIterator[str]:
        """Stream tokens from the LLM. Yields text strings."""
        yield ""  # pragma: no cover
