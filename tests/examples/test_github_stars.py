from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from examples.github_stars import github_stars


# Star counts returned by the mocked GitHub API, keyed by repo.
REPO_STAR_COUNTS = {
    "python/cpython": 60000,
    "microsoft/vscode": 160000,
}


@pytest.fixture
def mock_github_api():
    """Mock ``httpx.get`` so the example never hits the live GitHub API.

    The unauthenticated GitHub API is rate-limited to 60 requests/hour/IP, which
    makes the real call flaky on shared CI runners. The workflow calls
    ``httpx.get(url).json()["stargazers_count"]``, so we return a response whose
    ``.json()`` carries the expected star count for the requested repo.
    """

    def fake_get(url, *args, **kwargs):
        repo = url.split("/repos/", 1)[-1]
        response = MagicMock()
        response.json.return_value = {
            "stargazers_count": REPO_STAR_COUNTS.get(repo, 0),
        }
        return response

    with patch("httpx.get", side_effect=fake_get):
        yield


def test_should_succeed(mock_github_api):
    repos = list(REPO_STAR_COUNTS.keys())
    ctx = github_stars.run(repos)
    assert ctx.has_finished and ctx.has_succeeded, (
        "The workflow should have been completed successfully."
    )
    assert all(repo in ctx.output for repo in repos), (
        "The output should contain all the specified repositories."
    )
    for repo in repos:
        assert ctx.output[repo] == REPO_STAR_COUNTS[repo], (
            f"Expected {REPO_STAR_COUNTS[repo]} stars for {repo}, got {ctx.output[repo]}"
        )
    return ctx


def test_should_skip_if_finished(mock_github_api):
    first_ctx = test_should_succeed(mock_github_api)
    second_ctx = github_stars.run(execution_id=first_ctx.execution_id)
    assert first_ctx.execution_id == second_ctx.execution_id
    assert first_ctx.output == second_ctx.output


def test_should_fail_no_input():
    ctx = github_stars.run()
    assert ctx.has_finished and ctx.has_failed, "The workflow should have failed."


def test_should_fail_empty_list():
    ctx = github_stars.run([])
    assert ctx.has_finished and ctx.has_failed, "The workflow should have failed."
