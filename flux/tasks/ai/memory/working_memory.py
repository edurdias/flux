from __future__ import annotations

from typing import Any

from flux.domain.events import ExecutionEventType
from flux.domain.execution_context import CURRENT_CONTEXT
from flux.output_storage import OutputStorageReference


_TASK_PREFIX = "wm_memorize"


def _extract_value(event_value: Any) -> Any:
    """Extract the actual value from an event, handling OutputStorageReference."""
    if isinstance(event_value, OutputStorageReference):
        return event_value.metadata.get("value")
    if isinstance(event_value, dict) and "storage_type" in event_value:
        try:
            ref = OutputStorageReference.from_dict(event_value)
            return ref.metadata.get("value")
        except (KeyError, TypeError):
            return event_value
    return event_value


class WorkingMemory:
    def __init__(
        self,
        window: int | None = None,
        max_tokens: int | None = None,
    ) -> None:
        self._window = window
        self._max_tokens = max_tokens
        self._counter = 0

    async def memorize(self, role: str, content: str) -> None:
        """Store a message as a task event. Each call gets a unique task name."""
        from flux.task import task

        task_name = f"{_TASK_PREFIX}_{self._counter}"
        self._counter += 1

        @task.with_options(name=task_name)
        async def _store_message(role: str, content: str) -> dict[str, str]:
            return {"role": role, "content": content}

        await _store_message(role, content)

    def recall(self) -> list[dict[str, str]]:
        """Read all memorized messages from execution events, filtering out forgotten indices."""
        ctx = CURRENT_CONTEXT.get()
        if ctx is None:
            return []

        forgotten: set[int] = set()
        for event in ctx.events:
            if (
                event.type == ExecutionEventType.TASK_COMPLETED
                and event.name is not None
                and event.name.startswith("wm_forget")
            ):
                value = _extract_value(event.value)
                if isinstance(value, dict) and "forgotten_index" in value:
                    forgotten.add(value["forgotten_index"])

        messages: list[dict[str, str]] = []
        msg_index = 0
        for event in ctx.events:
            if (
                event.type == ExecutionEventType.TASK_COMPLETED
                and event.name is not None
                and event.name.startswith(_TASK_PREFIX)
            ):
                value = _extract_value(event.value)
                if isinstance(value, dict) and "role" in value and "content" in value:
                    if msg_index not in forgotten:
                        messages.append({"role": value["role"], "content": value["content"]})
                    msg_index += 1

        if self._window is not None and len(messages) > self._window:
            messages = messages[-self._window :]

        if self._max_tokens is not None:
            trimmed: list[dict[str, str]] = []
            token_count = 0
            for msg in reversed(messages):
                msg_tokens = len(msg["content"]) // 4
                if token_count + msg_tokens > self._max_tokens:
                    break
                trimmed.insert(0, msg)
                token_count += msg_tokens
            messages = trimmed

        return messages

    async def forget(self, index: int) -> None:
        """Mark a message at the given index as forgotten."""
        from flux.task import task

        task_name = f"wm_forget_{self._counter}"
        self._counter += 1

        @task.with_options(name=task_name)
        async def _forget_message(index: int) -> dict[str, Any]:
            return {"forgotten_index": index}

        await _forget_message(index)

    def keys(self) -> list[int]:
        """Return indices of all messages."""
        messages = self.recall()
        return list(range(len(messages)))
