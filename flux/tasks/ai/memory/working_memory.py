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
        self._counter: int | None = None

    def _next_counter(self) -> int:
        """Get the next counter value, initializing from existing events if needed."""
        if self._counter is None:
            ctx = CURRENT_CONTEXT.get()
            if ctx is None:
                self._counter = 0
            else:
                max_seen = -1
                for event in ctx.events:
                    if (
                        event.type == ExecutionEventType.TASK_COMPLETED
                        and event.name is not None
                        and (
                            event.name.startswith(_TASK_PREFIX)
                            or event.name.startswith("wm_forget")
                            or event.name.startswith("wm_compact")
                        )
                    ):
                        parts = event.name.rsplit("_", 1)
                        if parts[-1].isdigit():
                            max_seen = max(max_seen, int(parts[-1]))
                self._counter = max_seen + 1
        value = self._counter
        self._counter += 1
        return value

    async def memorize(self, role: str, content: str) -> None:
        """Store a message as a task event. Each call gets a unique task name."""
        from flux.task import task

        task_name = f"{_TASK_PREFIX}_{self._next_counter()}"

        @task.with_options(name=task_name)
        async def _store_message(role: str, content: str) -> dict[str, str]:
            return {"role": role, "content": content}

        await _store_message(role, content)

    def _collect_messages(self) -> list[dict[str, str]]:
        """Read all memorized messages from execution events, filtering out forgotten ones.

        Returns messages with their stable ID (task name) under the "_id" key.
        """
        ctx = CURRENT_CONTEXT.get()
        if ctx is None:
            return []

        forgotten: set[str] = set()
        compacted: set[str] = set()
        for event in ctx.events:
            if (
                event.type == ExecutionEventType.TASK_COMPLETED
                and event.name is not None
            ):
                if event.name.startswith("wm_forget"):
                    value = _extract_value(event.value)
                    if isinstance(value, dict) and "forgotten_id" in value:
                        forgotten.add(value["forgotten_id"])
                elif event.name.startswith("wm_compact"):
                    value = _extract_value(event.value)
                    if isinstance(value, dict) and "compacted_id" in value:
                        compacted.add(value["compacted_id"])

        excluded = forgotten | compacted

        messages: list[dict[str, str]] = []
        for event in ctx.events:
            if (
                event.type == ExecutionEventType.TASK_COMPLETED
                and event.name is not None
                and event.name.startswith(_TASK_PREFIX)
            ):
                if event.name in excluded:
                    continue
                value = _extract_value(event.value)
                if isinstance(value, dict) and "role" in value and "content" in value:
                    messages.append(
                        {
                            "_id": event.name,
                            "role": value["role"],
                            "content": value["content"],
                        },
                    )

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

    def recall(self) -> list[dict[str, str]]:
        """Read all memorized messages from execution events, filtering out forgotten ones."""
        return [
            {"role": msg["role"], "content": msg["content"]} for msg in self._collect_messages()
        ]

    async def forget(self, message_id: str) -> None:
        """Mark a message as forgotten by its stable ID (task name)."""
        from flux.task import task

        task_name = f"wm_forget_{self._next_counter()}"

        @task.with_options(name=task_name)
        async def _forget_message(forgotten_id: str) -> dict[str, Any]:
            return {"forgotten_id": forgotten_id}

        await _forget_message(message_id)

    async def _mark_compacted(self, message_id: str) -> None:
        """Mark a message as compacted by its stable ID (task name)."""
        from flux.task import task

        task_name = f"wm_compact_{self._next_counter()}"

        @task.with_options(name=task_name)
        async def _compact_message(compacted_id: str) -> dict[str, Any]:
            return {"compacted_id": compacted_id}

        await _compact_message(message_id)

    def keys(self) -> list[str]:
        """Return stable IDs of all messages in the current recall window."""
        return [msg["_id"] for msg in self._collect_messages()]
