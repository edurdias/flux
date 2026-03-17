from __future__ import annotations

from flux import ExecutionContext, task, workflow


@task
async def simple_task(x: int) -> int:
    return x * 2


def test_func_property_exposes_wrapped_function():
    assert simple_task.func is not None
    assert simple_task.func.__name__ == "simple_task"


def test_with_instance_options_returns_new_task():
    new_task = simple_task.with_instance_options(retry_max_attempts=5, timeout=30)
    assert isinstance(new_task, task)
    assert new_task is not simple_task


def test_with_instance_options_merges_values():
    new_task = simple_task.with_instance_options(retry_max_attempts=5, timeout=30)
    assert new_task.retry_max_attempts == 5
    assert new_task.timeout == 30
    assert new_task.retry_delay == simple_task.retry_delay
    assert new_task.retry_backoff == simple_task.retry_backoff


def test_with_instance_options_preserves_name():
    new_task = simple_task.with_instance_options(timeout=60)
    assert new_task.name == "simple_task"


def test_with_instance_options_overrides_name():
    new_task = simple_task.with_instance_options(name="custom_name")
    assert new_task.name == "custom_name"


def test_with_instance_options_task_still_works():
    @workflow
    async def test_workflow(ctx: ExecutionContext[int]):
        t = simple_task.with_instance_options(timeout=60)
        return await t(ctx.input)

    ctx = test_workflow.run(5)
    assert ctx.has_succeeded
    assert ctx.output == 10
