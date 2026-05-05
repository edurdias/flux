"""E2E tests — task feature matrix."""

from __future__ import annotations


def test_task_cache(cli):
    cli.register("examples/tasks/task_cache.py")
    r = cli.run("workflow_with_cached_task", "[2,3,3]")
    assert r["state"] == "COMPLETED"


def test_task_retries(cli):
    cli.register("examples/tasks/task_retries.py")
    r = cli.run("task_retries", "null", timeout=60)
    assert r["state"] == "COMPLETED"


def test_task_rollback(cli):
    cli.register("examples/tasks/task_rollback.py")
    r = cli.run("task_rollback", "null")
    assert r["state"] == "FAILED"  # designed to fail


def test_task_timeout(cli):
    cli.register("examples/tasks/task_timeout.py")
    r = cli.run("task_timeout", "null", timeout=45)
    assert r["state"] == "FAILED"  # designed to fail


def test_task_nested_timeout(cli):
    cli.register("examples/tasks/task_timeout.py")
    r = cli.run("task_nested_timeout", "null", timeout=45)
    assert r["state"] == "FAILED"  # designed to fail


def test_task_map(cli):
    cli.register("examples/tasks/task_map.py")
    r = cli.run_async_and_wait("task_map", "3", timeout=120)
    assert r["state"] == "COMPLETED"


def test_task_fallback(cli):
    cli.register("examples/tasks/task_fallback.py")
    r = cli.run("task_fallback", "null")
    assert r["state"] == "COMPLETED"


def test_task_fallback_after_retry(cli):
    cli.register("examples/tasks/task_fallback_after_retry.py")
    r = cli.run("task_fallback_after_retry", "null", timeout=60)
    assert r["state"] == "COMPLETED"


def test_task_fallback_after_timeout(cli):
    cli.register("examples/tasks/task_fallback_after_timeout.py")
    r = cli.run("task_fallback_after_timeout", "null", timeout=60)
    assert r["state"] == "COMPLETED"


def test_task_progress(cli):
    cli.register("examples/task_progress_example.py")
    r = cli.run("task_progress_example", '{"items": 5}')
    assert r["state"] == "COMPLETED"
