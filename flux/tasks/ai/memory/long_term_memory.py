from __future__ import annotations

from typing import Any

from flux.tasks.ai.memory.providers.protocol import MemoryProvider


class LongTermMemory:
    def __init__(self, provider: MemoryProvider, scope: str) -> None:
        self._provider = provider
        self._scope = scope

    def _get_workflow(self) -> str:
        from flux.domain.execution_context import CURRENT_CONTEXT

        ctx = CURRENT_CONTEXT.get()
        if ctx is None:
            return "__default__"
        return ctx.workflow_name

    async def memorize(self, key: str, value: Any) -> None:
        await self._provider.memorize(self._get_workflow(), self._scope, key, value)

    async def recall(self, key: str | None = None) -> Any:
        return await self._provider.recall(self._get_workflow(), self._scope, key)

    async def forget(self, key: str | None = None) -> None:
        await self._provider.forget(self._get_workflow(), self._scope, key)

    async def keys(self) -> list[str]:
        return await self._provider.keys(self._get_workflow(), self._scope)

    async def scopes(self) -> list[str]:
        return await self._provider.scopes(self._get_workflow())

    def as_tools(self) -> list:
        """Create Flux @task tools for agent integration."""
        from flux.task import task

        ltm = self

        @task
        async def recall_memory(key: str = "") -> str:
            """Retrieve stored facts from long-term memory. Pass a key to get a specific fact, or leave empty to get all facts."""
            result = await ltm.recall(key if key else None)
            if result is None:
                return "No memory found for that key."
            import json

            return json.dumps(result) if isinstance(result, dict) else str(result)

        @task
        async def store_memory(key: str, value: str) -> str:
            """Store a fact in long-term memory for future recall."""
            await ltm.memorize(key, value)
            return f"Stored: {key}"

        @task
        async def forget_memory(key: str = "") -> str:
            """Remove a fact from long-term memory. Pass a key to forget a specific fact, or leave empty to clear all."""
            await ltm.forget(key if key else None)
            return f"Forgotten: {key or 'all'}"

        @task
        async def list_memory_keys() -> str:
            """List all keys stored in long-term memory."""
            keys = await ltm.keys()
            import json

            return json.dumps(keys)

        return [recall_memory, store_memory, forget_memory, list_memory_keys]

    def system_prompt_hint(self) -> str:
        return (
            "\n\nYou have long-term memory. Use `recall_memory` to check for relevant context "
            "about the user or task. Use `store_memory` to save important facts worth remembering "
            "across conversations. Use `list_memory_keys` to see what you already know."
        )
