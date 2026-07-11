"""Repeated identical task calls are distinct calls (per-call occurrence ids)
and workflow-lifecycle flags ignore interleaved task events.

Before the occurrence fix, the replay short-circuit matched solely on the
argument-derived task_id, so the second ``await record(1)`` in one workflow
returned the first call's stored output without ever running the body —
side-effectful tasks silently ran once, ``now()`` twice returned the same
instant, and a second same-name ``pause()`` never paused.
"""

from __future__ import annotations

from flux import ExecutionContext, task as task_decorator, workflow
from flux.domain.events import ExecutionEvent, ExecutionEventType
from flux.tasks import pause


def test_identical_calls_each_run_the_body(isolated_db):
    calls: list[int] = []

    @task_decorator
    async def record(x: int) -> str:
        calls.append(x)
        return f"run-{len(calls)}"

    @workflow
    async def wf_dup(ctx: ExecutionContext):
        first = await record(1)
        second = await record(1)
        return [first, second]

    ctx = wf_dup.run()
    assert ctx.has_succeeded
    assert calls == [1, 1]
    assert ctx.output == ["run-1", "run-2"]
    # Each call carries its own terminal event under its own id.
    completed = [e for e in ctx.events if e.type == ExecutionEventType.TASK_COMPLETED]
    assert len(completed) == 2
    assert len({e.source_id for e in completed}) == 2


def test_identical_calls_replay_their_own_outputs(isolated_db):
    """Replay after a pause: the pre-pause call short-circuits (body does not
    re-run), the post-pause call is a fresh call and runs."""
    calls: list[int] = []

    @task_decorator
    async def record(x: int) -> str:
        calls.append(x)
        return f"run-{len(calls)}"

    @workflow
    async def wf_dup_pause(ctx: ExecutionContext):
        first = await record(1)
        await pause("gate")
        second = await record(1)
        return [first, second]

    ctx = wf_dup_pause.run()
    assert ctx.is_paused
    assert calls == [1]

    resumed = wf_dup_pause.run(execution_id=ctx.execution_id)
    assert resumed.has_succeeded
    # First call replayed from the log (body not re-run); second call ran.
    assert calls == [1, 1]
    assert resumed.output == ["run-1", "run-2"]


def test_cached_task_still_memoizes_identical_calls(isolated_db):
    """cache=True is opt-in memoization: the cache key stays the bare
    argument-derived id, so identical calls share the cached output while
    still emitting one event per call."""
    calls: list[int] = []

    @task_decorator.with_options(cache=True)
    async def cached(x: int) -> str:
        calls.append(x)
        return f"computed-{len(calls)}"

    @workflow
    async def wf_cached(ctx: ExecutionContext):
        first = await cached(5)
        second = await cached(5)
        return [first, second]

    ctx = wf_cached.run()
    assert ctx.has_succeeded
    assert calls == [5]  # memoized: body ran once
    assert ctx.output == ["computed-1", "computed-1"]


def _base_ctx() -> ExecutionContext:
    return ExecutionContext(
        workflow_id="default/wf",
        workflow_namespace="default",
        workflow_name="wf",
    )


def test_late_sibling_task_event_does_not_unfinish():
    """A parallel() sibling completing after WORKFLOW_FAILED lands (gather
    does not cancel siblings) must not flip the finished flags back."""
    ctx = _base_ctx()
    ctx.events.append(
        ExecutionEvent(type=ExecutionEventType.WORKFLOW_FAILED, source_id="w", name="wf"),
    )
    ctx.events.append(
        ExecutionEvent(type=ExecutionEventType.TASK_COMPLETED, source_id="late", name="sibling"),
    )
    assert ctx.has_finished
    assert ctx.has_failed
    assert not ctx.is_paused


def test_late_sibling_task_event_does_not_unpause():
    ctx = _base_ctx()
    ctx.events.append(
        ExecutionEvent(type=ExecutionEventType.WORKFLOW_PAUSED, source_id="w", name="wf"),
    )
    ctx.events.append(
        ExecutionEvent(type=ExecutionEventType.TASK_COMPLETED, source_id="late", name="sibling"),
    )
    assert ctx.is_paused
    assert not ctx.has_finished


def test_flags_follow_workflow_lifecycle_order():
    """PAUSED then RESUMED then COMPLETED reads as finished, not paused —
    the flags track the latest workflow-lifecycle event."""
    ctx = _base_ctx()
    for event_type in (
        ExecutionEventType.WORKFLOW_PAUSED,
        ExecutionEventType.WORKFLOW_RESUMED,
        ExecutionEventType.WORKFLOW_COMPLETED,
    ):
        ctx.events.append(ExecutionEvent(type=event_type, source_id="w", name="wf"))
    assert not ctx.is_paused
    assert ctx.has_finished
    assert ctx.has_succeeded


def test_wire_rebuilt_events_with_string_types_still_flag():
    """from_json rebuilds events with type as a plain str; the lifecycle
    flags must treat them like enum-typed events."""
    ctx = _base_ctx()
    ctx.events.append(ExecutionEvent(type="WORKFLOW_COMPLETED", source_id="w", name="wf"))  # type: ignore[arg-type]
    ctx.events.append(ExecutionEvent(type="TASK_COMPLETED", source_id="late", name="s"))  # type: ignore[arg-type]
    assert ctx.has_finished
