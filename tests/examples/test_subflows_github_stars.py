from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from examples.subflows import subflows
from flux.domain.events import ExecutionEventType


# Test data for different repositories and their star counts
REPO_STAR_COUNTS = {
    "python/cpython": 50000,
    "microsoft/vscode": 150000,
    "localsend/localsend": 25000,
    "other/repo": 5000,  # Default for any other repo
}


@pytest.fixture
def mock_httpx_client():
    """Fixture that creates and configures a mock for httpx.Client.

    This fixture mocks the HTTP client used by the 'call' task to call
    the get_stars_workflow. It returns mocked star counts for each repository.
    """
    # Create mock response
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()

    # Base response template
    mock_data = {
        "workflow_id": "get_stars_workflow",
        "workflow_name": "get_stars_workflow",
        "input": "",  # Will be replaced for each repo
        "execution_id": "mock-execution-id",
        "state": "completed",
        "events": [
            {
                "type": "WORKFLOW_COMPLETED",
                "source_id": "mock-source-id",
                "name": "get_stars_workflow",
                "value": 0,  # Will be replaced with the star count
            },
        ],
    }

    # Create side effect function to generate appropriate response for each repo
    def mock_post_side_effect(url, json, **kwargs):
        repo = json  # The repo name is passed as the JSON payload

        # Create a copy of the mock data with the repository-specific information
        response_data = mock_data.copy()
        response_data["input"] = repo

        # Set the star count based on the repository
        star_count = REPO_STAR_COUNTS.get(repo, REPO_STAR_COUNTS["other/repo"])
        response_data["events"][0]["value"] = star_count

        # Configure the mock response
        mock_response.json.return_value = response_data
        return mock_response

    # Create and configure the mock client
    with patch("httpx.Client") as mock_client:
        mock_client.return_value.__enter__.return_value.post.side_effect = mock_post_side_effect
        yield mock_client


def test_should_succeed(mock_httpx_client):
    """Test that the subflows workflow succeeds and returns correct star counts."""
    # Define test repositories
    repos = list(REPO_STAR_COUNTS.keys())[:3]  # Use first three repos

    # Run the workflow
    ctx = subflows.run(repos)

    # Verify the mock was called once for each repository
    assert mock_httpx_client.return_value.__enter__.return_value.post.call_count == len(repos)

    # Verify the workflow completed successfully
    assert (
        ctx.has_finished and ctx.has_succeeded
    ), f"The workflow should have been completed successfully, instead it finished with {ctx.state} state."

    # Verify all repositories are in the output
    assert all(
        repo in ctx.output for repo in repos
    ), "The output should contain all the specified repositories."

    # Verify the star counts match our expected values
    for repo in repos:
        assert (
            ctx.output[repo] == REPO_STAR_COUNTS[repo]
        ), f"Expected {REPO_STAR_COUNTS[repo]} stars for {repo}, but got {ctx.output[repo]}"

    return ctx


def test_should_skip_if_finished(mock_httpx_client):
    """Test that running a workflow with an existing execution_id skips execution."""
    # First execution
    first_ctx = test_should_succeed(mock_httpx_client)

    # Reset the mock to verify it's not called again
    mock_httpx_client.reset_mock()

    # Second execution with same execution_id
    second_ctx = subflows.run(execution_id=first_ctx.execution_id)

    # Verify execution was skipped (no additional HTTP calls)
    assert mock_httpx_client.return_value.__enter__.return_value.post.call_count == 0

    # Verify contexts match
    assert first_ctx.execution_id == second_ctx.execution_id
    assert first_ctx.output == second_ctx.output


@pytest.mark.parametrize(
    "input_value,error_type",
    [
        (None, TypeError),  # No input
        ([], TypeError),  # Empty list
    ],
)
def test_should_fail_with_invalid_input(input_value, error_type, mock_httpx_client):
    """Test that the workflow fails with appropriate errors for invalid inputs."""
    # Run workflow with the invalid input
    ctx = subflows.run(input_value)

    # Verify workflow failed
    assert ctx.has_failed, f"Workflow should have failed with input: {input_value}"

    # Verify the last event is a workflow failure
    last_event = ctx.events[-1]
    assert last_event.type == ExecutionEventType.WORKFLOW_FAILED

    # Verify the error type matches expected
    assert isinstance(
        last_event.value,
        error_type,
    ), f"Expected error of type {error_type.__name__}, but got {type(last_event.value).__name__}"
