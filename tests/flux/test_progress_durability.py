import asyncio

from flux.domain.events import ExecutionEventType
from flux.domain.execution_context import ExecutionContext
from flux.task import task
from flux.tasks.progress import progress


def test_progress_events_not_in_event_log():
    async def run():
        @task
        async def my_task():
            await progress({"step": 1})
            await progress({"step": 2})
            return "done"

        ctx = ExecutionContext(
            workflow_id="wf1",
            workflow_namespace="default",
            workflow_name="test",
        )
        ctx.set_progress_callback(lambda *_: None)
        token = ExecutionContext.set(ctx)
        try:
            await my_task()
        finally:
            ExecutionContext.reset(token)

        progress_events = [e for e in ctx.events if e.type == ExecutionEventType.TASK_PROGRESS]
        assert len(progress_events) == 0

        completed_events = [e for e in ctx.events if e.type == ExecutionEventType.TASK_COMPLETED]
        assert len(completed_events) == 1

    asyncio.run(run())


def test_replay_skips_task_with_progress():
    async def run():
        call_count = 0

        @task
        async def counting_task():
            nonlocal call_count
            call_count += 1
            await progress({"count": call_count})
            return f"result_{call_count}"

        ctx = ExecutionContext(
            workflow_id="wf1",
            workflow_namespace="default",
            workflow_name="test",
        )
        ctx.set_progress_callback(lambda *_: None)
        token = ExecutionContext.set(ctx)
        try:
            result1 = await counting_task()
        finally:
            ExecutionContext.reset(token)

        assert result1 == "result_1"
        assert call_count == 1

        token2 = ExecutionContext.set(ctx)
        try:
            result2 = await counting_task()
        finally:
            ExecutionContext.reset(token2)

        assert result2 == "result_1"
        assert call_count == 1

    asyncio.run(run())


def test_event_count_unchanged_with_streaming():
    async def run():
        @task
        async def chatty_task():
            for i in range(100):
                await progress({"token": f"word_{i}"})
            return "done"

        ctx = ExecutionContext(
            workflow_id="wf1",
            workflow_namespace="default",
            workflow_name="test",
        )
        ctx.set_progress_callback(lambda *_: None)
        token = ExecutionContext.set(ctx)
        try:
            await chatty_task()
        finally:
            ExecutionContext.reset(token)

        assert len(ctx.events) == 2
        assert ctx.events[0].type == ExecutionEventType.TASK_STARTED
        assert ctx.events[1].type == ExecutionEventType.TASK_COMPLETED

    asyncio.run(run())
