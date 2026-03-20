from __future__ import annotations

from flux.tasks.ai.memory.providers.in_memory import InMemoryProvider
from flux.tasks.ai.memory.providers.sqlite import SqliteProvider


def in_memory() -> InMemoryProvider:
    """Create an in-memory provider for testing."""
    return InMemoryProvider()


def sqlite(db_path: str) -> SqliteProvider:
    """Create a SQLite provider."""
    return SqliteProvider(db_path)


__all__ = ["in_memory", "sqlite"]
