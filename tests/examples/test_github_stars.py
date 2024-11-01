from __future__ import annotations

from examples.github_stars import github_stars


def test_should_succeed():
    repos = ["python/cpython", "microsoft/vscode"]
    ctx = github_stars.run(repos)
    assert ctx.finished and ctx.succeeded, "The workflow should have been completed successfully."
    assert all(
        repo in ctx.output for repo in repos
    ), "The output should contain all the specified repositories."
    return ctx


def test_should_skip_if_finished():
    first_ctx = test_should_succeed()
    second_ctx = github_stars.run(execution_id=first_ctx.execution_id)
    assert first_ctx.execution_id == second_ctx.execution_id
    assert first_ctx.output == second_ctx.output


def test_should_fail_no_input():
    ctx = github_stars.run()
    assert ctx.finished and ctx.failed, "The workflow should have failed."


def test_should_fail_empty_list():
    ctx = github_stars.run([])
    assert ctx.finished and ctx.failed, "The workflow should have failed."
