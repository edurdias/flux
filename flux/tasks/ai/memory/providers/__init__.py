from __future__ import annotations

from flux.tasks.ai.memory.providers.in_memory import InMemoryProvider
from flux.tasks.ai.memory.providers.protocol import MemoryProvider
from flux.tasks.ai.memory.providers.sqlite import SqliteProvider

try:
    from flux.tasks.ai.memory.providers.postgresql import PostgresqlProvider
except ImportError:
    PostgresqlProvider = None  # type: ignore[assignment,misc]

__all__ = ["InMemoryProvider", "MemoryProvider", "PostgresqlProvider", "SqliteProvider"]
