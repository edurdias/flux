from __future__ import annotations

import pytest

from flux.domain.events import ExecutionState
from flux.domain.execution_context import ExecutionContext
from flux.tasks.ai.memory.providers.in_memory import InMemoryProvider


def _make_ctx(workflow_name: str = "test_workflow"):  # type: ignore[no-untyped-def]
    ctx: ExecutionContext = ExecutionContext(
        workflow_id="wf1",
        workflow_name=workflow_name,
        state=ExecutionState.RUNNING,
    )
    token = ExecutionContext.set(ctx)
    return ctx, token


@pytest.mark.asyncio
async def test_ltm_memorize_and_recall():
    from flux.tasks.ai.memory.long_term_memory import LongTermMemory

    ctx, token = _make_ctx()
    try:
        provider = InMemoryProvider()
        ltm = LongTermMemory(provider=provider, scope="user:1")
        await ltm.memorize("name", "Eduardo")
        result = await ltm.recall("name")
        assert result == "Eduardo"
    finally:
        ExecutionContext.reset(token)


@pytest.mark.asyncio
async def test_ltm_recall_all():
    from flux.tasks.ai.memory.long_term_memory import LongTermMemory

    ctx, token = _make_ctx()
    try:
        provider = InMemoryProvider()
        ltm = LongTermMemory(provider=provider, scope="user:1")
        await ltm.memorize("name", "Eduardo")
        await ltm.memorize("role", "VP")
        result = await ltm.recall()
        assert result == {"name": "Eduardo", "role": "VP"}
    finally:
        ExecutionContext.reset(token)


@pytest.mark.asyncio
async def test_ltm_forget():
    from flux.tasks.ai.memory.long_term_memory import LongTermMemory

    ctx, token = _make_ctx()
    try:
        provider = InMemoryProvider()
        ltm = LongTermMemory(provider=provider, scope="user:1")
        await ltm.memorize("name", "Eduardo")
        await ltm.forget("name")
        result = await ltm.recall("name")
        assert result is None
    finally:
        ExecutionContext.reset(token)


@pytest.mark.asyncio
async def test_ltm_keys():
    from flux.tasks.ai.memory.long_term_memory import LongTermMemory

    ctx, token = _make_ctx()
    try:
        provider = InMemoryProvider()
        ltm = LongTermMemory(provider=provider, scope="user:1")
        await ltm.memorize("name", "Eduardo")
        await ltm.memorize("role", "VP")
        keys = await ltm.keys()
        assert sorted(keys) == ["name", "role"]
    finally:
        ExecutionContext.reset(token)


@pytest.mark.asyncio
async def test_ltm_scopes():
    from flux.tasks.ai.memory.long_term_memory import LongTermMemory

    ctx, token = _make_ctx()
    try:
        provider = InMemoryProvider()
        ltm1 = LongTermMemory(provider=provider, scope="user:1")
        ltm2 = LongTermMemory(provider=provider, scope="user:2")
        await ltm1.memorize("name", "Eduardo")
        await ltm2.memorize("name", "Alice")
        scopes = await ltm1.scopes()
        assert sorted(scopes) == ["user:1", "user:2"]
    finally:
        ExecutionContext.reset(token)


@pytest.mark.asyncio
async def test_ltm_workflow_auto_scoping():
    """Workflow name is auto-injected from ExecutionContext."""
    from flux.tasks.ai.memory.long_term_memory import LongTermMemory

    ctx, token = _make_ctx("my_workflow")
    try:
        provider = InMemoryProvider()
        ltm = LongTermMemory(provider=provider, scope="user:1")
        await ltm.memorize("name", "Eduardo")
        result = await provider.recall("my_workflow", "user:1", "name")
        assert result == "Eduardo"
    finally:
        ExecutionContext.reset(token)


@pytest.mark.asyncio
async def test_ltm_tools_generation():
    """LTM generates tool definitions for agent integration."""
    from flux.tasks.ai.memory.long_term_memory import LongTermMemory

    provider = InMemoryProvider()
    ltm = LongTermMemory(provider=provider, scope="user:1")
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
    ltm = LongTermMemory(provider=provider, scope="user:1")
    hint = ltm.system_prompt_hint()
    assert "recall_memory" in hint
    assert "store_memory" in hint
