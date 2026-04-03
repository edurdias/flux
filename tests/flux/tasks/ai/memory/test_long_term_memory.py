from __future__ import annotations

import pytest

from flux.tasks.ai.memory.providers.in_memory import InMemoryProvider


@pytest.mark.asyncio
async def test_ltm_memorize_and_recall():
    from flux.tasks.ai.memory.long_term_memory import LongTermMemory

    provider = InMemoryProvider()
    ltm = LongTermMemory(provider=provider, agent="assistant", scope="user:1")
    await ltm.memorize("name", "Alice")
    result = await ltm.recall("name")
    assert result == "Alice"


@pytest.mark.asyncio
async def test_ltm_recall_all():
    from flux.tasks.ai.memory.long_term_memory import LongTermMemory

    provider = InMemoryProvider()
    ltm = LongTermMemory(provider=provider, agent="assistant", scope="user:1")
    await ltm.memorize("name", "Alice")
    await ltm.memorize("role", "developer")
    result = await ltm.recall()
    assert result == {"name": "Alice", "role": "developer"}


@pytest.mark.asyncio
async def test_ltm_forget():
    from flux.tasks.ai.memory.long_term_memory import LongTermMemory

    provider = InMemoryProvider()
    ltm = LongTermMemory(provider=provider, agent="assistant", scope="user:1")
    await ltm.memorize("name", "Alice")
    await ltm.forget("name")
    result = await ltm.recall("name")
    assert result is None


@pytest.mark.asyncio
async def test_ltm_keys():
    from flux.tasks.ai.memory.long_term_memory import LongTermMemory

    provider = InMemoryProvider()
    ltm = LongTermMemory(provider=provider, agent="assistant", scope="user:1")
    await ltm.memorize("name", "Alice")
    await ltm.memorize("role", "developer")
    keys = await ltm.keys()
    assert sorted(keys) == ["name", "role"]


@pytest.mark.asyncio
async def test_ltm_scopes():
    from flux.tasks.ai.memory.long_term_memory import LongTermMemory

    provider = InMemoryProvider()
    ltm1 = LongTermMemory(provider=provider, agent="assistant", scope="user:1")
    ltm2 = LongTermMemory(provider=provider, agent="assistant", scope="user:2")
    await ltm1.memorize("name", "Alice")
    await ltm2.memorize("name", "Bob")
    scopes = await ltm1.scopes()
    assert sorted(scopes) == ["user:1", "user:2"]


@pytest.mark.asyncio
async def test_ltm_agent_scoping():
    """Agent name is passed explicitly and reaches the provider directly."""
    from flux.tasks.ai.memory.long_term_memory import LongTermMemory

    provider = InMemoryProvider()
    ltm = LongTermMemory(provider=provider, agent="my_agent", scope="user:1")
    await ltm.memorize("name", "Alice")
    result = await provider.recall("my_agent", "user:1", "name")
    assert result == "Alice"


@pytest.mark.asyncio
async def test_ltm_agent_property():
    """LTM exposes the agent name via a property."""
    from flux.tasks.ai.memory.long_term_memory import LongTermMemory

    provider = InMemoryProvider()
    ltm = LongTermMemory(provider=provider, agent="my_agent", scope="user:1")
    assert ltm.agent == "my_agent"


@pytest.mark.asyncio
async def test_ltm_scope_property():
    """LTM exposes the scope via a property."""
    from flux.tasks.ai.memory.long_term_memory import LongTermMemory

    provider = InMemoryProvider()
    ltm = LongTermMemory(provider=provider, agent="assistant", scope="user:1")
    assert ltm.scope == "user:1"


@pytest.mark.asyncio
async def test_ltm_tools_generation():
    """LTM generates tool definitions for agent integration."""
    from flux.tasks.ai.memory.long_term_memory import LongTermMemory

    provider = InMemoryProvider()
    ltm = LongTermMemory(provider=provider, agent="assistant", scope="user:1")
    tools = ltm.as_tools()
    tool_names = [t.func.__name__ if hasattr(t, "func") else t.__name__ for t in tools]
    assert "recall_memory" in tool_names
    assert "store_memory" in tool_names
    assert "forget_memory" in tool_names
    assert "list_memory_keys" in tool_names


@pytest.mark.asyncio
async def test_ltm_system_prompt_hint():
    """LTM generates a system prompt hint for agents."""
    from flux.tasks.ai.memory.long_term_memory import LongTermMemory

    provider = InMemoryProvider()
    ltm = LongTermMemory(provider=provider, agent="assistant", scope="user:1")
    hint = ltm.system_prompt_hint()
    assert "recall_memory" in hint
    assert "store_memory" in hint
