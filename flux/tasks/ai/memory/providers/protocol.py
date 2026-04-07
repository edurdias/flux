from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class MemoryProvider(Protocol):
    async def memorize(self, agent: str, scope: str, key: str, value: Any) -> None:
        ...

    async def recall(self, agent: str, scope: str, key: str | None = None) -> Any:
        ...

    async def forget(self, agent: str, scope: str, key: str | None = None) -> None:
        ...

    async def keys(self, agent: str, scope: str) -> list[str]:
        ...

    async def scopes(self, agent: str) -> list[str]:
        ...
