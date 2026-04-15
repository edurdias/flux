"""Tests for MCP elicitation types."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from flux.tasks.mcp.elicitation import ElicitationRequestOutput, ElicitationResponse


class TestElicitationRequestOutput:
    def test_url_mode(self):
        output = ElicitationRequestOutput(
            elicitation_id="abc-123",
            url="https://auth.example.com/oauth",
            message="Please authorize access.",
            server_name="github-mcp",
        )
        assert output.type == "elicitation"
        assert output.mode == "url"
        assert output.elicitation_id == "abc-123"
        assert output.url == "https://auth.example.com/oauth"

    def test_serialization_roundtrip(self):
        output = ElicitationRequestOutput(
            elicitation_id="abc-123",
            url="https://auth.example.com/oauth",
            message="Authorize.",
            server_name="github",
        )
        data = output.model_dump()
        assert data["type"] == "elicitation"
        assert data["mode"] == "url"
        restored = ElicitationRequestOutput(**data)
        assert restored == output

    def test_type_is_literal(self):
        output = ElicitationRequestOutput(
            elicitation_id="x",
            url="https://example.com",
            message="Auth.",
            server_name="s",
        )
        assert output.type == "elicitation"


class TestElicitationResponse:
    def test_accept(self):
        response = ElicitationResponse(
            elicitation_id="abc-123",
            action="accept",
        )
        assert response.action == "accept"
        assert response.elicitation_id == "abc-123"

    def test_decline(self):
        response = ElicitationResponse(
            elicitation_id="abc-123",
            action="decline",
        )
        assert response.action == "decline"

    def test_cancel(self):
        response = ElicitationResponse(
            elicitation_id="abc-123",
            action="cancel",
        )
        assert response.action == "cancel"

    def test_invalid_action(self):
        with pytest.raises(ValidationError):
            ElicitationResponse(
                elicitation_id="abc-123",
                action="invalid",
            )

    def test_serialization(self):
        response = ElicitationResponse(
            elicitation_id="abc-123",
            action="accept",
        )
        data = response.model_dump()
        assert data["action"] == "accept"
