"""Tests for MCP tool builder elicitation handling."""

from __future__ import annotations

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
                }
            ]
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
