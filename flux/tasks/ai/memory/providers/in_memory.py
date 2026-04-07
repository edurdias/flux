from __future__ import annotations

from typing import Any


class InMemoryProvider:
    def __init__(self) -> None:
        self._store: dict[str, dict[str, dict[str, Any]]] = {}

    def _get_scope(self, agent: str, scope: str) -> dict[str, Any]:
        return self._store.setdefault(agent, {}).setdefault(scope, {})

    async def memorize(self, agent: str, scope: str, key: str, value: Any) -> None:
        self._get_scope(agent, scope)[key] = value

    async def recall(self, agent: str, scope: str, key: str | None = None) -> Any:
        scope_data = self._get_scope(agent, scope)
        if key is None:
            return dict(scope_data)
        return scope_data.get(key)

    async def forget(self, agent: str, scope: str, key: str | None = None) -> None:
        scope_data = self._get_scope(agent, scope)
        if key is None:
            scope_data.clear()
        else:
            scope_data.pop(key, None)

    async def keys(self, agent: str, scope: str) -> list[str]:
        return list(self._get_scope(agent, scope).keys())

    async def scopes(self, agent: str) -> list[str]:
        return list(self._store.get(agent, {}).keys())
