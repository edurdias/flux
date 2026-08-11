"""Tests for MCP tool builder elicitation handling."""

from __future__ import annotations

import pytest

from flux.tasks.mcp.elicitation import ElicitationRequestOutput


class MockElicitationError(Exception):
    """Simulates a -32042 error from fastmcp."""

    def __init__(self, elicitation_id, url, message):
        self.code = -32042
        self.data = {
            "elicitations": [
                {
                    "mode": "url",
                    "elicitationId": elicitation_id,
                    "url": url,
                    "message": message,
                },
            ],
        }
        super().__init__(message)


def test_elicitation_error_triggers_pause():
    from flux.tasks.mcp.tool_builder import _handle_elicitation_error

    error = MockElicitationError(
        elicitation_id="elic-123",
        url="https://auth.example.com/oauth",
        message="Authorization required",
    )

    result = _handle_elicitation_error(error, server_name="github-mcp")
    assert isinstance(result, ElicitationRequestOutput)
    assert result.elicitation_id == "elic-123"
    assert result.url == "https://auth.example.com/oauth"
    assert result.server_name == "github-mcp"


def test_elicitation_error_without_data_reraises():
    from flux.tasks.mcp.tool_builder import _handle_elicitation_error

    error = Exception("some error")
    error.code = -32042

    try:
        _handle_elicitation_error(error, server_name="test")
        assert False, "Should have raised"
    except Exception as e:
        assert e is error


def test_elicitation_request_output_structure():
    output = ElicitationRequestOutput(
        elicitation_id="x",
        url="https://example.com",
        message="Auth needed",
        server_name="test",
    )
    data = output.model_dump()
    assert data["type"] == "elicitation"
    assert data["mode"] == "url"


def test_extract_elicitation_action_accept_via_nested():
    from flux.tasks.mcp.tool_builder import _extract_elicitation_action

    assert (
        _extract_elicitation_action(
            {"elicitation_response": {"elicitation_id": "e1", "action": "accept"}},
        )
        == "accept"
    )


def test_extract_elicitation_action_decline_via_nested():
    from flux.tasks.mcp.tool_builder import _extract_elicitation_action

    assert (
        _extract_elicitation_action(
            {"elicitation_response": {"elicitation_id": "e1", "action": "decline"}},
        )
        == "decline"
    )


def test_extract_elicitation_action_cancel_via_nested():
    from flux.tasks.mcp.tool_builder import _extract_elicitation_action

    assert (
        _extract_elicitation_action(
            {"elicitation_response": {"elicitation_id": "e1", "action": "cancel"}},
        )
        == "cancel"
    )


def test_extract_elicitation_action_direct_shape():
    from flux.tasks.mcp.tool_builder import _extract_elicitation_action

    assert _extract_elicitation_action({"action": "accept"}) == "accept"


def test_extract_elicitation_action_missing_defaults_decline():
    from flux.tasks.mcp.tool_builder import _extract_elicitation_action

    assert _extract_elicitation_action(None) == "decline"
    assert _extract_elicitation_action({}) == "decline"
    assert _extract_elicitation_action({"foo": "bar"}) == "decline"


DANGEROUS_URLS = [
    "javascript:fetch('/approval/1',{method:'POST'})",
    "file:///home/op/.flux/bootstrap_token",
    "smb://attacker/share",
    "data:text/html,<script>1</script>",
    "vscode://file/etc/passwd",
]


@pytest.mark.parametrize("url", DANGEROUS_URLS)
def test_dangerous_elicitation_schemes_are_refused(url):
    """The URL comes from a remote server's error payload and reaches a.href
    in the web UI and webbrowser.open in both terminal UIs. Only http(s) is a
    browsable authorization page; anything else is a URI handler the operator
    never asked to invoke."""
    from flux.tasks.mcp.tool_builder import _handle_elicitation_error

    error = MockElicitationError("elic-1", url, "Authorization required")
    with pytest.raises(Exception) as excinfo:
        _handle_elicitation_error(error, server_name="evil-mcp")
    assert excinfo.value is error


@pytest.mark.parametrize("url", ["https://auth.example.com/oauth", "http://localhost:8080/cb"])
def test_browsable_schemes_are_accepted(url):
    from flux.tasks.mcp.tool_builder import _handle_elicitation_error

    result = _handle_elicitation_error(
        MockElicitationError("elic-1", url, "Authorization required"),
        server_name="github-mcp",
    )
    assert result.url == url


def test_model_refuses_a_non_browsable_url_directly():
    """Enforced on the model too, so any other construction site fails closed."""
    with pytest.raises(ValueError):
        ElicitationRequestOutput(
            elicitation_id="e1",
            url="javascript:alert(1)",
            message="m",
            server_name="s",
        )
