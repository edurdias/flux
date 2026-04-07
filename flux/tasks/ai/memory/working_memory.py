from __future__ import annotations

from typing import Any

from flux.domain.events import ExecutionEventType
from flux.domain.execution_context import CURRENT_CONTEXT
from flux.output_storage import OutputStorageReference


_TASK_PREFIX = "wm_memorize"

COMPACT_PROMPT = (
    "Summarize the following conversation into a structured summary. "
    "Preserve all important information.\n\n"
    "Your summary must cover these sections:\n"
    "1. **User Requests** — what the user asked for\n"
    "2. **Key Decisions** — technology choices, approach pivots, configuration changes\n"
    "3. **Tools Used** — which tools were called and key results (not full output)\n"
    "4. **Errors and Fixes** — problems encountered and how they were resolved\n"
    "5. **Current State** — what was being worked on at the point of compaction\n"
    "6. **Pending Tasks** — anything explicitly requested but not yet completed\n\n"
    "Be concise but preserve all facts that would be needed to continue the work."
)


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
        compact_model: str | None = None,
        compact_threshold: float = 0.70,
        compact_preserve: int = 4,
    ) -> None:
        self._window = window
        self._max_tokens = max_tokens
        self._compact_model = compact_model
        self._compact_threshold = compact_threshold
        self._compact_preserve = compact_preserve
        self._counter: int | None = None
        self._compacting: bool = False

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

    def _estimate_tokens(self, messages: list[dict[str, str]]) -> int:
        return sum(max(1, len(m["content"]) // 4) for m in messages)

    def _should_compact(self) -> bool:
        if self._compact_model is None or self._max_tokens is None:
            return False
        messages = self._collect_messages()
        tokens = self._estimate_tokens(messages)
        return tokens > self._max_tokens * self._compact_threshold

    async def _prune_tool_results(self) -> None:
        import json

        messages = self._collect_messages()
        if len(messages) <= self._compact_preserve:
            return

        prunable = messages[: -self._compact_preserve]
        for msg in prunable:
            if msg["role"] != "tool_result":
                continue
            try:
                data = json.loads(msg["content"])
            except (json.JSONDecodeError, TypeError):
                continue
            output = data.get("output", "")
            if len(output) <= 200:
                continue
            data["output"] = f"[truncated] {data.get('name', 'tool')}: {output[:200]}..."
            await self._mark_compacted(
                msg["_id"],
                replacement={"role": "tool_result", "content": json.dumps(data)},
            )

    async def _summarize_messages(self) -> None:
        messages = self._collect_messages()
        if len(messages) <= self._compact_preserve:
            return

        to_summarize = messages[: -self._compact_preserve]
        if not to_summarize:
            return

        from flux.tasks.ai import agent

        assert self._compact_model is not None
        compact_agent = await agent(
            COMPACT_PROMPT,
            model=self._compact_model,
            name="wm_compact_summarizer",
            stream=False,
        )

        formatted = "\n".join(f"[{m['role']}] {m['content']}" for m in to_summarize)
        summary = await compact_agent(formatted)

        for msg in to_summarize:
            await self._mark_compacted(msg["_id"])

        await self.memorize("assistant", summary)

    async def memorize(self, role: str, content: str) -> None:
        from flux.task import task

        task_name = f"{_TASK_PREFIX}_{self._next_counter()}"

        @task.with_options(name=task_name)
        async def _store_message(role: str, content: str) -> dict[str, str]:
            return {"role": role, "content": content}

        await _store_message(role, content)

        if not self._compacting and self._should_compact():
            self._compacting = True
            try:
                await self._prune_tool_results()
                if self._should_compact():
                    await self._summarize_messages()
            finally:
                self._compacting = False

    def _collect_messages(self) -> list[dict[str, str]]:
        """Read all memorized messages from execution events, filtering out forgotten ones.

        Returns messages with their stable ID (task name) under the "_id" key.
        """
        ctx = CURRENT_CONTEXT.get()
        if ctx is None:
            return []

        forgotten: set[str] = set()
        compacted: set[str] = set()
        replacements: dict[str, dict[str, str]] = {}
        for event in ctx.events:
            if event.type == ExecutionEventType.TASK_COMPLETED and event.name is not None:
                if event.name.startswith("wm_forget"):
                    value = _extract_value(event.value)
                    if isinstance(value, dict) and "forgotten_id" in value:
                        forgotten.add(value["forgotten_id"])
                elif event.name.startswith("wm_compact"):
                    value = _extract_value(event.value)
                    if isinstance(value, dict) and "compacted_id" in value:
                        cid = value["compacted_id"]
                        if "replacement" in value:
                            replacements[cid] = value["replacement"]
                        else:
                            compacted.add(cid)

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
                if event.name in replacements:
                    r = replacements[event.name]
                    messages.append(
                        {"_id": event.name, "role": r["role"], "content": r["content"]},
                    )
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

    async def _mark_compacted(
        self,
        message_id: str,
        replacement: dict[str, str] | None = None,
    ) -> None:
        """Mark a message as compacted. Optionally provide a replacement that
        keeps the original position in the message sequence."""
        from flux.task import task

        task_name = f"wm_compact_{self._next_counter()}"

        @task.with_options(name=task_name)
        async def _compact_message(
            compacted_id: str,
            replacement: dict[str, str] | None = None,
        ) -> dict[str, Any]:
            result: dict[str, Any] = {"compacted_id": compacted_id}
            if replacement is not None:
                result["replacement"] = replacement
            return result

        await _compact_message(compacted_id=message_id, replacement=replacement)

    def keys(self) -> list[str]:
        """Return stable IDs of all messages in the current recall window."""
        return [msg["_id"] for msg in self._collect_messages()]
