from __future__ import annotations

import os
import tempfile

import pytest


@pytest.mark.asyncio
async def test_provider_memorize_and_recall():
    """Provider stores a value and recalls it by key."""
    from flux.tasks.ai.memory.providers.in_memory import InMemoryProvider

    provider = InMemoryProvider()
    await provider.memorize("agent_id", "user:1", "name", "Alice")
    result = await provider.recall("agent_id", "user:1", "name")
    assert result == "Alice"


@pytest.mark.asyncio
async def test_provider_recall_all_in_scope():
    """Recall without key returns all entries as dict."""
    from flux.tasks.ai.memory.providers.in_memory import InMemoryProvider

    provider = InMemoryProvider()
    await provider.memorize("agent_id", "user:1", "name", "Alice")
    await provider.memorize("agent_id", "user:1", "role", "developer")
    result = await provider.recall("agent_id", "user:1")
    assert result == {"name": "Alice", "role": "developer"}


@pytest.mark.asyncio
async def test_provider_recall_missing_key_returns_none():
    from flux.tasks.ai.memory.providers.in_memory import InMemoryProvider

    provider = InMemoryProvider()
    result = await provider.recall("agent_id", "user:1", "missing")
    assert result is None


@pytest.mark.asyncio
async def test_provider_recall_empty_scope_returns_empty_dict():
    from flux.tasks.ai.memory.providers.in_memory import InMemoryProvider

    provider = InMemoryProvider()
    result = await provider.recall("agent_id", "user:1")
    assert result == {}


@pytest.mark.asyncio
async def test_provider_memorize_overwrites():
    """Memorize with existing key updates the value."""
    from flux.tasks.ai.memory.providers.in_memory import InMemoryProvider

    provider = InMemoryProvider()
    await provider.memorize("agent_id", "user:1", "role", "Engineer")
    await provider.memorize("agent_id", "user:1", "role", "developer")
    result = await provider.recall("agent_id", "user:1", "role")
    assert result == "developer"


@pytest.mark.asyncio
async def test_provider_forget_key():
    from flux.tasks.ai.memory.providers.in_memory import InMemoryProvider

    provider = InMemoryProvider()
    await provider.memorize("agent_id", "user:1", "name", "Alice")
    await provider.forget("agent_id", "user:1", "name")
    result = await provider.recall("agent_id", "user:1", "name")
    assert result is None


@pytest.mark.asyncio
async def test_provider_forget_scope():
    """Forget without key clears entire scope."""
    from flux.tasks.ai.memory.providers.in_memory import InMemoryProvider

    provider = InMemoryProvider()
    await provider.memorize("agent_id", "user:1", "name", "Alice")
    await provider.memorize("agent_id", "user:1", "role", "developer")
    await provider.forget("agent_id", "user:1")
    result = await provider.recall("agent_id", "user:1")
    assert result == {}


@pytest.mark.asyncio
async def test_provider_keys():
    from flux.tasks.ai.memory.providers.in_memory import InMemoryProvider

    provider = InMemoryProvider()
    await provider.memorize("agent_id", "user:1", "name", "Alice")
    await provider.memorize("agent_id", "user:1", "role", "developer")
    keys = await provider.keys("agent_id", "user:1")
    assert sorted(keys) == ["name", "role"]


@pytest.mark.asyncio
async def test_provider_scopes():
    from flux.tasks.ai.memory.providers.in_memory import InMemoryProvider

    provider = InMemoryProvider()
    await provider.memorize("agent_id", "user:1", "name", "Alice")
    await provider.memorize("agent_id", "user:2", "name", "Bob")
    scopes = await provider.scopes("agent_id")
    assert sorted(scopes) == ["user:1", "user:2"]


@pytest.mark.asyncio
async def test_provider_agent_isolation():
    """Different agents don't see each other's data."""
    from flux.tasks.ai.memory.providers.in_memory import InMemoryProvider

    provider = InMemoryProvider()
    await provider.memorize("agent_a", "user:1", "name", "Alice")
    await provider.memorize("agent_b", "user:1", "name", "Bob")
    assert await provider.recall("agent_a", "user:1", "name") == "Alice"
    assert await provider.recall("agent_b", "user:1", "name") == "Bob"


def test_in_memory_provider_conforms_to_protocol():
    from flux.tasks.ai.memory.providers.in_memory import InMemoryProvider
    from flux.tasks.ai.memory.providers.protocol import MemoryProvider

    assert isinstance(InMemoryProvider(), MemoryProvider)


def test_memory_entry_fields():
    from flux.tasks.ai.memory.types import MemoryEntry

    entry = MemoryEntry(agent="agent_id", scope="user:1", key="name", value="Alice")
    assert entry.agent == "agent_id"
    assert entry.scope == "user:1"
    assert entry.key == "name"
    assert entry.value == "Alice"


@pytest.mark.asyncio
async def test_sqlalchemy_provider_memorize_and_recall():
    from flux.tasks.ai.memory.providers.sqlalchemy import SqlAlchemyProvider

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        provider = SqlAlchemyProvider(f"sqlite:///{db_path}")
        await provider.memorize("agent_id", "user:1", "name", "Alice")
        result = await provider.recall("agent_id", "user:1", "name")
        assert result == "Alice"


@pytest.mark.asyncio
async def test_sqlalchemy_provider_recall_all():
    from flux.tasks.ai.memory.providers.sqlalchemy import SqlAlchemyProvider

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        provider = SqlAlchemyProvider(f"sqlite:///{db_path}")
        await provider.memorize("agent_id", "user:1", "name", "Alice")
        await provider.memorize("agent_id", "user:1", "role", "developer")
        result = await provider.recall("agent_id", "user:1")
        assert result == {"name": "Alice", "role": "developer"}


@pytest.mark.asyncio
async def test_sqlalchemy_provider_upsert():
    from flux.tasks.ai.memory.providers.sqlalchemy import SqlAlchemyProvider

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        provider = SqlAlchemyProvider(f"sqlite:///{db_path}")
        await provider.memorize("agent_id", "user:1", "role", "Engineer")
        await provider.memorize("agent_id", "user:1", "role", "developer")
        result = await provider.recall("agent_id", "user:1", "role")
        assert result == "developer"


@pytest.mark.asyncio
async def test_sqlalchemy_provider_forget_key():
    from flux.tasks.ai.memory.providers.sqlalchemy import SqlAlchemyProvider

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        provider = SqlAlchemyProvider(f"sqlite:///{db_path}")
        await provider.memorize("agent_id", "user:1", "name", "Alice")
        await provider.forget("agent_id", "user:1", "name")
        result = await provider.recall("agent_id", "user:1", "name")
        assert result is None


@pytest.mark.asyncio
async def test_sqlalchemy_provider_forget_scope():
    from flux.tasks.ai.memory.providers.sqlalchemy import SqlAlchemyProvider

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        provider = SqlAlchemyProvider(f"sqlite:///{db_path}")
        await provider.memorize("agent_id", "user:1", "name", "Alice")
        await provider.memorize("agent_id", "user:1", "role", "developer")
        await provider.forget("agent_id", "user:1")
        result = await provider.recall("agent_id", "user:1")
        assert result == {}


@pytest.mark.asyncio
async def test_sqlalchemy_provider_keys_and_scopes():
    from flux.tasks.ai.memory.providers.sqlalchemy import SqlAlchemyProvider

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        provider = SqlAlchemyProvider(f"sqlite:///{db_path}")
        await provider.memorize("agent_id", "user:1", "name", "Alice")
        await provider.memorize("agent_id", "user:2", "name", "Bob")
        assert sorted(await provider.keys("agent_id", "user:1")) == ["name"]
        assert sorted(await provider.scopes("agent_id")) == ["user:1", "user:2"]


@pytest.mark.asyncio
async def test_sqlalchemy_provider_agent_isolation():
    from flux.tasks.ai.memory.providers.sqlalchemy import SqlAlchemyProvider

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        provider = SqlAlchemyProvider(f"sqlite:///{db_path}")
        await provider.memorize("agent_a", "user:1", "name", "Alice")
        await provider.memorize("agent_b", "user:1", "name", "Bob")
        assert await provider.recall("agent_a", "user:1", "name") == "Alice"
        assert await provider.recall("agent_b", "user:1", "name") == "Bob"


@pytest.mark.asyncio
async def test_sqlalchemy_provider_persists_across_instances():
    """Data survives creating a new provider instance with same db."""
    from flux.tasks.ai.memory.providers.sqlalchemy import SqlAlchemyProvider

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        provider1 = SqlAlchemyProvider(f"sqlite:///{db_path}")
        await provider1.memorize("agent_id", "user:1", "name", "Alice")
        provider2 = SqlAlchemyProvider(f"sqlite:///{db_path}")
        result = await provider2.recall("agent_id", "user:1", "name")
        assert result == "Alice"
