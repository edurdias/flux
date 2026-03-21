from __future__ import annotations

import os
import tempfile

import pytest


@pytest.mark.asyncio
async def test_provider_memorize_and_recall():
    """Provider stores a value and recalls it by key."""
    from flux.tasks.ai.memory.providers.in_memory import InMemoryProvider

    provider = InMemoryProvider()
    await provider.memorize("wf", "user:1", "name", "Eduardo")
    result = await provider.recall("wf", "user:1", "name")
    assert result == "Eduardo"


@pytest.mark.asyncio
async def test_provider_recall_all_in_scope():
    """Recall without key returns all entries as dict."""
    from flux.tasks.ai.memory.providers.in_memory import InMemoryProvider

    provider = InMemoryProvider()
    await provider.memorize("wf", "user:1", "name", "Eduardo")
    await provider.memorize("wf", "user:1", "role", "VP")
    result = await provider.recall("wf", "user:1")
    assert result == {"name": "Eduardo", "role": "VP"}


@pytest.mark.asyncio
async def test_provider_recall_missing_key_returns_none():
    from flux.tasks.ai.memory.providers.in_memory import InMemoryProvider

    provider = InMemoryProvider()
    result = await provider.recall("wf", "user:1", "missing")
    assert result is None


@pytest.mark.asyncio
async def test_provider_recall_empty_scope_returns_empty_dict():
    from flux.tasks.ai.memory.providers.in_memory import InMemoryProvider

    provider = InMemoryProvider()
    result = await provider.recall("wf", "user:1")
    assert result == {}


@pytest.mark.asyncio
async def test_provider_memorize_overwrites():
    """Memorize with existing key updates the value."""
    from flux.tasks.ai.memory.providers.in_memory import InMemoryProvider

    provider = InMemoryProvider()
    await provider.memorize("wf", "user:1", "role", "Engineer")
    await provider.memorize("wf", "user:1", "role", "VP")
    result = await provider.recall("wf", "user:1", "role")
    assert result == "VP"


@pytest.mark.asyncio
async def test_provider_forget_key():
    from flux.tasks.ai.memory.providers.in_memory import InMemoryProvider

    provider = InMemoryProvider()
    await provider.memorize("wf", "user:1", "name", "Eduardo")
    await provider.forget("wf", "user:1", "name")
    result = await provider.recall("wf", "user:1", "name")
    assert result is None


@pytest.mark.asyncio
async def test_provider_forget_scope():
    """Forget without key clears entire scope."""
    from flux.tasks.ai.memory.providers.in_memory import InMemoryProvider

    provider = InMemoryProvider()
    await provider.memorize("wf", "user:1", "name", "Eduardo")
    await provider.memorize("wf", "user:1", "role", "VP")
    await provider.forget("wf", "user:1")
    result = await provider.recall("wf", "user:1")
    assert result == {}


@pytest.mark.asyncio
async def test_provider_keys():
    from flux.tasks.ai.memory.providers.in_memory import InMemoryProvider

    provider = InMemoryProvider()
    await provider.memorize("wf", "user:1", "name", "Eduardo")
    await provider.memorize("wf", "user:1", "role", "VP")
    keys = await provider.keys("wf", "user:1")
    assert sorted(keys) == ["name", "role"]


@pytest.mark.asyncio
async def test_provider_scopes():
    from flux.tasks.ai.memory.providers.in_memory import InMemoryProvider

    provider = InMemoryProvider()
    await provider.memorize("wf", "user:1", "name", "Eduardo")
    await provider.memorize("wf", "user:2", "name", "Alice")
    scopes = await provider.scopes("wf")
    assert sorted(scopes) == ["user:1", "user:2"]


@pytest.mark.asyncio
async def test_provider_workflow_isolation():
    """Different workflows don't see each other's data."""
    from flux.tasks.ai.memory.providers.in_memory import InMemoryProvider

    provider = InMemoryProvider()
    await provider.memorize("wf_a", "user:1", "name", "Eduardo")
    await provider.memorize("wf_b", "user:1", "name", "Alice")
    assert await provider.recall("wf_a", "user:1", "name") == "Eduardo"
    assert await provider.recall("wf_b", "user:1", "name") == "Alice"


def test_in_memory_provider_conforms_to_protocol():
    from flux.tasks.ai.memory.providers.in_memory import InMemoryProvider
    from flux.tasks.ai.memory.providers.protocol import MemoryProvider

    assert isinstance(InMemoryProvider(), MemoryProvider)


def test_memory_entry_fields():
    from flux.tasks.ai.memory.types import MemoryEntry

    entry = MemoryEntry(workflow="wf", scope="user:1", key="name", value="Eduardo")
    assert entry.workflow == "wf"
    assert entry.scope == "user:1"
    assert entry.key == "name"
    assert entry.value == "Eduardo"


@pytest.mark.asyncio
async def test_sqlalchemy_provider_memorize_and_recall():
    from flux.tasks.ai.memory.providers.sqlalchemy import SqlAlchemyProvider

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        provider = SqlAlchemyProvider(f"sqlite:///{db_path}")
        await provider.memorize("wf", "user:1", "name", "Eduardo")
        result = await provider.recall("wf", "user:1", "name")
        assert result == "Eduardo"


@pytest.mark.asyncio
async def test_sqlalchemy_provider_recall_all():
    from flux.tasks.ai.memory.providers.sqlalchemy import SqlAlchemyProvider

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        provider = SqlAlchemyProvider(f"sqlite:///{db_path}")
        await provider.memorize("wf", "user:1", "name", "Eduardo")
        await provider.memorize("wf", "user:1", "role", "VP")
        result = await provider.recall("wf", "user:1")
        assert result == {"name": "Eduardo", "role": "VP"}


@pytest.mark.asyncio
async def test_sqlalchemy_provider_upsert():
    from flux.tasks.ai.memory.providers.sqlalchemy import SqlAlchemyProvider

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        provider = SqlAlchemyProvider(f"sqlite:///{db_path}")
        await provider.memorize("wf", "user:1", "role", "Engineer")
        await provider.memorize("wf", "user:1", "role", "VP")
        result = await provider.recall("wf", "user:1", "role")
        assert result == "VP"


@pytest.mark.asyncio
async def test_sqlalchemy_provider_forget_key():
    from flux.tasks.ai.memory.providers.sqlalchemy import SqlAlchemyProvider

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        provider = SqlAlchemyProvider(f"sqlite:///{db_path}")
        await provider.memorize("wf", "user:1", "name", "Eduardo")
        await provider.forget("wf", "user:1", "name")
        result = await provider.recall("wf", "user:1", "name")
        assert result is None


@pytest.mark.asyncio
async def test_sqlalchemy_provider_forget_scope():
    from flux.tasks.ai.memory.providers.sqlalchemy import SqlAlchemyProvider

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        provider = SqlAlchemyProvider(f"sqlite:///{db_path}")
        await provider.memorize("wf", "user:1", "name", "Eduardo")
        await provider.memorize("wf", "user:1", "role", "VP")
        await provider.forget("wf", "user:1")
        result = await provider.recall("wf", "user:1")
        assert result == {}


@pytest.mark.asyncio
async def test_sqlalchemy_provider_keys_and_scopes():
    from flux.tasks.ai.memory.providers.sqlalchemy import SqlAlchemyProvider

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        provider = SqlAlchemyProvider(f"sqlite:///{db_path}")
        await provider.memorize("wf", "user:1", "name", "Eduardo")
        await provider.memorize("wf", "user:2", "name", "Alice")
        assert sorted(await provider.keys("wf", "user:1")) == ["name"]
        assert sorted(await provider.scopes("wf")) == ["user:1", "user:2"]


@pytest.mark.asyncio
async def test_sqlalchemy_provider_workflow_isolation():
    from flux.tasks.ai.memory.providers.sqlalchemy import SqlAlchemyProvider

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        provider = SqlAlchemyProvider(f"sqlite:///{db_path}")
        await provider.memorize("wf_a", "user:1", "name", "Eduardo")
        await provider.memorize("wf_b", "user:1", "name", "Alice")
        assert await provider.recall("wf_a", "user:1", "name") == "Eduardo"
        assert await provider.recall("wf_b", "user:1", "name") == "Alice"


@pytest.mark.asyncio
async def test_sqlalchemy_provider_persists_across_instances():
    """Data survives creating a new provider instance with same db."""
    from flux.tasks.ai.memory.providers.sqlalchemy import SqlAlchemyProvider

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        provider1 = SqlAlchemyProvider(f"sqlite:///{db_path}")
        await provider1.memorize("wf", "user:1", "name", "Eduardo")
        provider2 = SqlAlchemyProvider(f"sqlite:///{db_path}")
        result = await provider2.recall("wf", "user:1", "name")
        assert result == "Eduardo"
