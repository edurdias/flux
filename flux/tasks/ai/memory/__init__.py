from __future__ import annotations

from flux.tasks.ai.memory.providers.in_memory import InMemoryProvider
from flux.tasks.ai.memory.providers.sqlite import SqliteProvider


def in_memory() -> InMemoryProvider:
    """Create an in-memory provider for testing."""
    return InMemoryProvider()


def sqlite(db_path: str) -> SqliteProvider:
    """Create a SQLite provider."""
    return SqliteProvider(db_path)


def postgresql(connection_string: str):
    """Create a PostgreSQL provider."""
    from flux.tasks.ai.memory.providers.postgresql import PostgresqlProvider

    return PostgresqlProvider(connection_string)


__all__ = ["in_memory", "postgresql", "sqlite"]
