"""Regression tests: task decorator / __init__ must not share mutable default lists."""

from __future__ import annotations

from flux.task import task


def test_distinct_tasks_have_independent_secret_requests_lists():
    @task
    async def a():
        pass

    @task
    async def b():
        pass

    assert a.secret_requests == []
    assert b.secret_requests == []
    assert (
        a.secret_requests is not b.secret_requests
    ), "Mutable default leaked: two @task-decorated functions share the same list."


def test_distinct_tasks_have_independent_config_requests_lists():
    @task
    async def a():
        pass

    @task
    async def b():
        pass

    assert a.config_requests == []
    assert b.config_requests == []
    assert (
        a.config_requests is not b.config_requests
    ), "Mutable default leaked: two @task-decorated functions share the same list."


def test_mutating_one_task_secret_requests_does_not_affect_another():
    @task
    async def a():
        pass

    @task
    async def b():
        pass

    a.secret_requests.append("leaked:key")
    assert (
        b.secret_requests == []
    ), f"State leaked across task instances: b.secret_requests={b.secret_requests!r}"


def test_mutating_one_task_config_requests_does_not_affect_another():
    @task
    async def a():
        pass

    @task
    async def b():
        pass

    a.config_requests.append("leaked:key")
    assert (
        b.config_requests == []
    ), f"State leaked across task instances: b.config_requests={b.config_requests!r}"


def test_with_options_creates_independent_lists_from_defaults():
    @task.with_options()
    async def a():
        pass

    @task.with_options()
    async def b():
        pass

    assert a.secret_requests is not b.secret_requests
    assert a.config_requests is not b.config_requests


def test_explicit_empty_list_in_decorator_not_shared():
    @task.with_options(config_requests=[])
    async def a():
        pass

    @task.with_options(config_requests=[])
    async def b():
        pass

    a.config_requests.append("x")
    assert b.config_requests == []
