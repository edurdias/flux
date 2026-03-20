from __future__ import annotations

from flux.tasks.ai.memory.providers.in_memory import InMemoryProvider


def in_memory() -> InMemoryProvider:
    """Create an in-memory provider for testing."""
    return InMemoryProvider()


__all__ = ["in_memory"]
