from __future__ import annotations

from examples.github_stars import github_stars
from flux.events import ExecutionEventType


def test_should_succeed():
    repos = [
        'python/cpython',
        'microsoft/vscode',
        'localsend/localsend',
        'srush/GPU-Puzzles',
        'hyperknot/openfreemap',
    ]
    ctx = github_stars.run(repos)
    assert (
        ctx.finished and ctx.succeeded
    ), 'The workflow should have been completed successfully.'
    assert all(
        repo in ctx.output for repo in repos
    ), 'The output should contain all the specified repositories.'


def test_should_replay():
    repos = [
        'python/cpython',
        'microsoft/vscode',
        'localsend/localsend',
        'srush/GPU-Puzzles',
        'hyperknot/openfreemap',
    ]
    first_ctx = github_stars.run(repos)
    assert (
        first_ctx.finished and first_ctx.succeeded
    ), 'The workflow should have been completed successfully.'

    second_ctx = github_stars.run(execution_id=first_ctx.execution_id)
    assert first_ctx.events[-1] == second_ctx.events[-1]


def test_should_fail_no_input():
    ctx = github_stars.run()
    last_event = ctx.events[-1]
    assert last_event.type == ExecutionEventType.WORKFLOW_FAILED
    assert isinstance(last_event.value, TypeError)


def test_should_fail_empty_list():
    ctx = github_stars.run([])
    last_event = ctx.events[-1]
    assert last_event.type == ExecutionEventType.WORKFLOW_FAILED
    assert isinstance(last_event.value, TypeError)
