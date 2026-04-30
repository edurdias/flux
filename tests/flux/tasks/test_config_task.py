"""Tests for config_requests support in task and get_config task."""

from __future__ import annotations


from flux.task import task


def test_task_has_config_requests_attribute():
    @task.with_options(config_requests=["app:setting"])
    async def my_task(config: dict | None = None):
        return config or {}

    assert my_task.config_requests == ["app:setting"]


def test_task_default_config_requests_empty():
    @task
    async def my_task():
        pass

    assert my_task.config_requests == []


def test_with_options_overrides_config_requests():
    @task.with_options(config_requests=["a"])
    async def my_task(config: dict | None = None):
        return config or {}

    new_task = my_task.with_options(config_requests=["b", "c"])
    assert new_task.config_requests == ["b", "c"]


def test_with_options_inherits_config_requests():
    @task.with_options(config_requests=["a"])
    async def my_task(config: dict | None = None):
        return config or {}

    new_task = my_task.with_options(name="renamed")
    assert new_task.config_requests == ["a"]


def test_get_config_task_exists():
    from flux.tasks.config_task import get_config

    assert get_config is not None
    assert hasattr(get_config, "config_requests")
