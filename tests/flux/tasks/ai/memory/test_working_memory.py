from __future__ import annotations

from typing import Any

import pytest

from flux.domain.events import ExecutionState
from flux.domain.execution_context import ExecutionContext


def _make_ctx() -> tuple[ExecutionContext, Any]:
    ctx: ExecutionContext = ExecutionContext(
        workflow_id="wf1",
        workflow_name="test_workflow",
        state=ExecutionState.RUNNING,
    )
    token = ExecutionContext.set(ctx)
    return ctx, token


@pytest.mark.asyncio
async def test_working_memory_memorize_and_recall():
    from flux.tasks.ai.memory.working_memory import WorkingMemory

    ctx, token = _make_ctx()
    try:
        wm = WorkingMemory()
        await wm.memorize("user", "hello")
        await wm.memorize("assistant", "hi there")
        messages = wm.recall()
        assert len(messages) == 2
        assert messages[0] == {"role": "user", "content": "hello"}
        assert messages[1] == {"role": "assistant", "content": "hi there"}
    finally:
        ExecutionContext.reset(token)


@pytest.mark.asyncio
async def test_working_memory_recall_empty():
    from flux.tasks.ai.memory.working_memory import WorkingMemory

    ctx, token = _make_ctx()
    try:
        wm = WorkingMemory()
        messages = wm.recall()
        assert messages == []
    finally:
        ExecutionContext.reset(token)


@pytest.mark.asyncio
async def test_working_memory_window():
    from flux.tasks.ai.memory.working_memory import WorkingMemory

    ctx, token = _make_ctx()
    try:
        wm = WorkingMemory(window=2)
        await wm.memorize("user", "msg1")
        await wm.memorize("assistant", "reply1")
        await wm.memorize("user", "msg2")
        await wm.memorize("assistant", "reply2")
        await wm.memorize("user", "msg3")
        await wm.memorize("assistant", "reply3")
        messages = wm.recall()
        assert len(messages) == 2
        assert messages[0] == {"role": "user", "content": "msg3"}
        assert messages[1] == {"role": "assistant", "content": "reply3"}
    finally:
        ExecutionContext.reset(token)


@pytest.mark.asyncio
async def test_working_memory_unique_task_ids():
    """Duplicate messages should not be deduplicated."""
    from flux.tasks.ai.memory.working_memory import WorkingMemory

    ctx, token = _make_ctx()
    try:
        wm = WorkingMemory()
        await wm.memorize("user", "yes")
        await wm.memorize("assistant", "ok")
        await wm.memorize("user", "yes")
        await wm.memorize("assistant", "ok")
        messages = wm.recall()
        assert len(messages) == 4
    finally:
        ExecutionContext.reset(token)


@pytest.mark.asyncio
async def test_working_memory_recall_from_events():
    """Recall reconstructs from events (simulates replay)."""
    from flux.tasks.ai.memory.working_memory import WorkingMemory

    ctx, token = _make_ctx()
    try:
        wm = WorkingMemory()
        await wm.memorize("user", "hello")
        await wm.memorize("assistant", "hi")

        wm2 = WorkingMemory()
        messages = wm2.recall()
        assert len(messages) == 2
        assert messages[0]["content"] == "hello"
        assert messages[1]["content"] == "hi"
    finally:
        ExecutionContext.reset(token)


@pytest.mark.asyncio
async def test_working_memory_keys_returns_stable_ids():
    """keys() returns task names that survive windowing."""
    from flux.tasks.ai.memory.working_memory import WorkingMemory

    ctx, token = _make_ctx()
    try:
        wm = WorkingMemory()
        await wm.memorize("user", "hello")
        await wm.memorize("assistant", "hi")
        ids = wm.keys()
        assert len(ids) == 2
        assert all(id.startswith("wm_memorize_") for id in ids)
    finally:
        ExecutionContext.reset(token)


@pytest.mark.asyncio
async def test_working_memory_forget_by_stable_id():
    """forget() uses stable task name IDs, not fragile indices."""
    from flux.tasks.ai.memory.working_memory import WorkingMemory

    ctx, token = _make_ctx()
    try:
        wm = WorkingMemory()
        await wm.memorize("user", "msg1")
        await wm.memorize("assistant", "reply1")
        await wm.memorize("user", "msg2")
        await wm.memorize("assistant", "reply2")

        ids = wm.keys()
        assert len(ids) == 4

        # Forget the first message by its stable ID
        await wm.forget(ids[0])

        messages = wm.recall()
        assert len(messages) == 3
        assert messages[0] == {"role": "assistant", "content": "reply1"}
    finally:
        ExecutionContext.reset(token)
