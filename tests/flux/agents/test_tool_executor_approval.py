"""Tests for the post-migration tool_executor approval behavior.

Task 18 deletes the inline ``pause(...)``-based approval block from
``flux/tasks/ai/tool_executor.py`` and instead relies on the engine-level
``task.requires_approval`` gate (Tasks 8-11). When the agent runs in
``autonomous`` mode, tool_executor must propagate that intent down to the
engine by setting ``ctx.approval_bypass = True`` so the gate is skipped
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


def test_autonomous_mode_propagates_bypass_to_context():
    """approval_mode='autonomous' must set ctx.approval_bypass=True for the
    duration of the batch so the engine skips the requires_approval gate
    for tools invoked from here. The flag is restored on exit (see
    ``test_autonomous_bypass_does_not_leak_into_subsequent_batches``)."""

    @workflow
    async def test_wf(ctx: ExecutionContext):
        results = await execute_tools(
            [{"id": "c1", "name": "gated_tool", "arguments": {"target": "prod"}}],
            [gated_tool],
            approval_mode="autonomous",
        )
        # Post-batch the bypass is restored. The behaviour we want to check
        # is that the gated tool actually ran — i.e. the bypass was active
        # while the tool was invoked.
        return {"bypass_after": ctx.approval_bypass, "results": results}

    ctx = test_wf.run()
    assert ctx.has_succeeded, [e.type for e in ctx.events]
    assert ctx.output["bypass_after"] is False
    assert ctx.output["results"][0]["output"] == "deleted:prod"


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
        results = await execute_tools(
            [{"id": "c1", "name": "benign_tool", "arguments": {"query": "hi"}}],
            [benign_tool],
            approval_mode="autonomous",
        )
        return {"bypass_after": ctx.approval_bypass, "results": results}

    ctx = test_wf.run()
    assert ctx.has_succeeded
    assert ctx.output["bypass_after"] is False
    assert ctx.output["results"][0]["output"] == "result:hi"


def test_default_mode_does_not_set_bypass():
    @workflow
    async def test_wf(ctx: ExecutionContext):
        results = await execute_tools(
            [{"id": "c1", "name": "benign_tool", "arguments": {"query": "hi"}}],
            [benign_tool],
        )
        return {"bypass": ctx.approval_bypass, "results": results}

    ctx = test_wf.run()
    assert ctx.has_succeeded
    assert ctx.output["bypass"] is False


def test_autonomous_bypass_does_not_leak_into_subsequent_batches():
    """Regression: a single autonomous batch must not flip the bypass flag
    on for the rest of the workflow. Subsequent gated tools (run in default
    mode by another execute_tools call) should still pause for approval."""
    from flux.domain.events import ExecutionEventType

    @workflow
    async def test_wf(ctx: ExecutionContext):
        await execute_tools(
            [{"id": "c1", "name": "benign_tool", "arguments": {"query": "hi"}}],
            [benign_tool],
            approval_mode="autonomous",
        )
        # After the autonomous batch, bypass must be back to False so the
        # next gated tool (default mode) actually pauses.
        assert ctx.approval_bypass is False
        return await execute_tools(
            [{"id": "c2", "name": "gated_tool", "arguments": {"target": "prod"}}],
            [gated_tool],
        )

    ctx = test_wf.run()
    assert ctx.is_paused
    awaiting = [e for e in ctx.events if e.type == ExecutionEventType.TASK_AWAITING_APPROVAL]
    assert len(awaiting) == 1
