from __future__ import annotations

from flux.tasks.ai.memory.long_term_memory import LongTermMemory
from flux.tasks.ai.memory.providers.in_memory import InMemoryProvider
from flux.tasks.ai.memory.providers.sqlalchemy import SqlAlchemyProvider
from flux.tasks.ai.memory.working_memory import WorkingMemory


def in_memory() -> InMemoryProvider:
    """Create an in-memory provider for testing."""
    return InMemoryProvider()


def sqlite(db_path: str) -> SqlAlchemyProvider:
    """Create a SQLite provider."""
    return SqlAlchemyProvider(f"sqlite:///{db_path}")


def postgresql(connection_string: str) -> SqlAlchemyProvider:
    """Create a PostgreSQL provider."""
    return SqlAlchemyProvider(connection_string)


def long_term_memory(provider: InMemoryProvider | SqlAlchemyProvider, scope: str) -> LongTermMemory:
    """Create a long-term memory backed by a provider."""
    return LongTermMemory(provider=provider, scope=scope)


def working_memory(
    window: int | None = None,
    max_tokens: int | None = None,
) -> WorkingMemory:
    """Create a working memory for conversation history."""
    return WorkingMemory(window=window, max_tokens=max_tokens)


__all__ = [
    "in_memory",
    "long_term_memory",
    "LongTermMemory",
    "postgresql",
    "sqlite",
    "working_memory",
    "WorkingMemory",
]
