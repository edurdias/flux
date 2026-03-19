import asyncio

from flux._task_context import _CURRENT_TASK
from flux.task import task
from flux.domain.execution_context import ExecutionContext


def test_current_task_contextvar_set_during_execution():
    captured_task_info = []

    @task
    async def capture_task():
        info = _CURRENT_TASK.get()
        captured_task_info.append(info)
        return "done"

    async def run():
        ctx = ExecutionContext(workflow_id="wf1", workflow_name="test")
        token = ExecutionContext.set(ctx)
        try:
            await capture_task()
        finally:
            ExecutionContext.reset(token)

    asyncio.run(run())
    assert captured_task_info[0] is not None
    task_id, task_name = captured_task_info[0]
    assert task_name == "capture_task"
    assert "capture_task" in task_id


def test_concurrent_tasks_get_independent_identities():
    captured = {}

    @task
    async def identify_self(label: str):
        info = _CURRENT_TASK.get()
        captured[label] = info
        await asyncio.sleep(0.01)
        assert _CURRENT_TASK.get() == info
        return label

    async def run():
        ctx = ExecutionContext(workflow_id="wf1", workflow_name="test")
        token = ExecutionContext.set(ctx)
        try:
            await asyncio.gather(
                identify_self("a"),
                identify_self("b"),
            )
        finally:
            ExecutionContext.reset(token)

    asyncio.run(run())
    assert captured["a"] is not None
    assert captured["b"] is not None
    assert captured["a"] != captured["b"]


def test_current_task_contextvar_cleared_after_execution():
    @task
    async def simple_task():
        return "done"

    async def run():
        ctx = ExecutionContext(workflow_id="wf1", workflow_name="test")
        token = ExecutionContext.set(ctx)
        try:
            await simple_task()
        finally:
            ExecutionContext.reset(token)
        return _CURRENT_TASK.get()

    result = asyncio.run(run())
    assert result is None
