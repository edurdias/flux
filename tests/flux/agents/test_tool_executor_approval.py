"""Tests for the post-migration tool_executor approval behavior.

The inline ``pause(...)``-based approval block was removed from
``flux/tasks/ai/tool_executor.py`` in favour of the engine-level
``task.requires_approval`` gate. When the agent runs in ``autonomous``
mode, tool_executor runs each tool as a
``with_options(requires_approval=False)`` variant so the gate is skipped
for tools invoked through this code path.
"""

from __future__ import annotations

import inspect

from flux import ExecutionContext, task, workflow
from flux.tasks.ai.tool_executor import execute_tools


@task.with_options(requires_approval=True)
async def gated_tool(target: str) -> str:
    return f"deleted:{target}"


@task
async def benign_tool(query: str) -> str:
    return f"result:{query}"


def test_old_inline_approval_block_is_deleted():
    """tool_executor.py must no longer use the pause-based approval pattern.

    The deleted block injected an output payload tagged ``tool_approval`` and
    used a pause name prefixed with ``tool_approval:``. Both are sentinels of
    the old flow and must not appear in source.
    """
    import flux.tasks.ai.tool_executor as te

    src = inspect.getsource(te)
    assert '"type": "tool_approval"' not in src
    assert "tool_approval:" not in src


def test_autonomous_mode_skips_gate():
    """approval_mode='autonomous' runs each tool as a non-gated variant, so a
    gated tool runs to completion instead of pausing for approval."""

    @workflow
    async def test_wf(ctx: ExecutionContext):
        return await execute_tools(
            [{"id": "c1", "name": "gated_tool", "arguments": {"target": "prod"}}],
            [gated_tool],
            approval_mode="autonomous",
        )

    ctx = test_wf.run()
    assert ctx.has_succeeded, [e.type for e in ctx.events]
    assert ctx.output[0]["output"] == "deleted:prod"


def test_default_mode_lets_engine_gate_pause_workflow():
    """In the default approval mode tool_executor MUST NOT short-circuit the
    engine gate. A gated tool should pause the workflow via the engine
    primitive (TASK_AWAITING_APPROVAL → WORKFLOW_PAUSED)."""
    from flux.domain.events import ExecutionEventType

    @workflow
    async def test_wf(ctx: ExecutionContext):
        return await execute_tools(
            [{"id": "c1", "name": "gated_tool", "arguments": {"target": "prod"}}],
            [gated_tool],
        )

    ctx = test_wf.run()
    assert ctx.is_paused
    awaiting = [e for e in ctx.events if e.type == ExecutionEventType.TASK_AWAITING_APPROVAL]
    assert len(awaiting) == 1
    assert awaiting[0].name == "gated_tool"


def test_non_gated_tools_unaffected_in_autonomous_mode():
    """Tools without requires_approval run normally regardless of mode."""

    @workflow
    async def test_wf(ctx: ExecutionContext):
        return await execute_tools(
            [{"id": "c1", "name": "benign_tool", "arguments": {"query": "hi"}}],
            [benign_tool],
            approval_mode="autonomous",
        )

    ctx = test_wf.run()
    assert ctx.has_succeeded
    assert ctx.output[0]["output"] == "result:hi"


def test_autonomous_batch_does_not_disable_later_gate():
    """Regression: an autonomous batch must not disable approval for a later
    default-mode gated tool. With per-batch with_options variants there is no
    shared bypass state, so the next gated tool still pauses."""
    from flux.domain.events import ExecutionEventType

    @workflow
    async def test_wf(ctx: ExecutionContext):
        await execute_tools(
            [{"id": "c1", "name": "benign_tool", "arguments": {"query": "hi"}}],
            [benign_tool],
            approval_mode="autonomous",
        )
        return await execute_tools(
            [{"id": "c2", "name": "gated_tool", "arguments": {"target": "prod"}}],
            [gated_tool],
        )

    ctx = test_wf.run()
    assert ctx.is_paused
    awaiting = [e for e in ctx.events if e.type == ExecutionEventType.TASK_AWAITING_APPROVAL]
    assert len(awaiting) == 1
