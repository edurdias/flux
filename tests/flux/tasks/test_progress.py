import asyncio

from flux.domain.execution_context import ExecutionContext
from flux._task_context import _CURRENT_TASK
from flux.task import task


def test_progress_import():
    from flux.tasks import progress

    assert callable(progress)


def test_progress_calls_emit_progress_on_context():
    captured = []

    def on_progress(execution_id, task_id, task_name, value):
        captured.append(
            {
                "execution_id": execution_id,
                "task_id": task_id,
                "task_name": task_name,
                "value": value,
            },
        )

    async def run():
        from flux.tasks.progress import progress

        ctx = ExecutionContext(workflow_id="wf1", workflow_namespace="default", workflow_name="test", execution_id="exec_1")
        ctx.set_progress_callback(on_progress)
        token = ExecutionContext.set(ctx)
        task_token = _CURRENT_TASK.set(("task_abc", "my_task"))
        try:
            await progress({"step": 1, "total": 10})
        finally:
            _CURRENT_TASK.reset(task_token)
            ExecutionContext.reset(token)

    asyncio.run(run())
    assert len(captured) == 1
    assert captured[0]["execution_id"] == "exec_1"
    assert captured[0]["task_id"] == "task_abc"
    assert captured[0]["task_name"] == "my_task"
    assert captured[0]["value"] == {"step": 1, "total": 10}


def test_progress_noop_without_current_task():
    captured = []

    def on_progress(execution_id, task_id, task_name, value):
        captured.append(value)

    async def run():
        from flux.tasks.progress import progress

        ctx = ExecutionContext(workflow_id="wf1", workflow_namespace="default", workflow_name="test")
        ctx.set_progress_callback(on_progress)
        token = ExecutionContext.set(ctx)
        try:
            await progress({"step": 1})
        finally:
            ExecutionContext.reset(token)

    asyncio.run(run())
    assert len(captured) == 0


def test_progress_inside_task():
    captured = []

    def on_progress(execution_id, task_id, task_name, value):
        captured.append({"task_name": task_name, "value": value})

    async def run():
        from flux.tasks.progress import progress

        @task
        async def my_processing_task(items: int):
            for i in range(items):
                await progress({"processed": i + 1, "total": items})
            return "done"

        ctx = ExecutionContext(workflow_id="wf1", workflow_namespace="default", workflow_name="test")
        ctx.set_progress_callback(on_progress)
        token = ExecutionContext.set(ctx)
        try:
            result = await my_processing_task(3)
        finally:
            ExecutionContext.reset(token)

        assert result == "done"

    asyncio.run(run())
    assert len(captured) == 3
    assert captured[0]["task_name"] == "my_processing_task"
    assert captured[0]["value"] == {"processed": 1, "total": 3}
    assert captured[2]["value"] == {"processed": 3, "total": 3}


def test_progress_accepts_any_value_type():
    captured = []

    def on_progress(execution_id, task_id, task_name, value):
        captured.append(value)

    async def run():
        from flux.tasks.progress import progress

        ctx = ExecutionContext(workflow_id="wf1", workflow_namespace="default", workflow_name="test")
        ctx.set_progress_callback(on_progress)
        token = ExecutionContext.set(ctx)
        task_token = _CURRENT_TASK.set(("t1", "test_task"))
        try:
            await progress("simple string")
            await progress(42)
            await progress({"token": "hello"})
            await progress(["a", "b"])
        finally:
            _CURRENT_TASK.reset(task_token)
            ExecutionContext.reset(token)

    asyncio.run(run())
    assert captured == ["simple string", 42, {"token": "hello"}, ["a", "b"]]
