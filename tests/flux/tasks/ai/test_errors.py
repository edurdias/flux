"""Friendly provider-error mapping (issue #141C)."""

from __future__ import annotations

import pytest

from flux.errors import ExecutionError, ModelProviderError
from flux.tasks.ai.errors import friendly_model_error, wrap_provider_errors


class _StatusError(Exception):
    def __init__(self, message: str, status_code: int):
        super().__init__(message)
        self.status_code = status_code


class TestFriendlyModelError:
    def test_auth_by_status_code(self):
        text = friendly_model_error("openai/gpt-4o", _StatusError("nope", 401))
        assert "Authentication" in text and "openai" in text
        assert "_StatusError: nope" in text  # raw error preserved

    def test_auth_by_message_marker(self):
        text = friendly_model_error("anthropic/claude-x", Exception("invalid x-api-key"))
        assert "Authentication" in text

    def test_model_not_found(self):
        text = friendly_model_error("openai/gpt-nope", _StatusError("model_not_found", 404))
        assert "not available" in text and "gpt-nope" in text

    def test_rate_limit(self):
        text = friendly_model_error("openai/gpt-4o", _StatusError("Too many requests", 429))
        assert "rate-limited" in text

    def test_context_length(self):
        text = friendly_model_error(
            "anthropic/claude-x",
            Exception("prompt is too long: maximum context length exceeded"),
        )
        assert "context window" in text

    def test_unreachable_endpoint(self):
        text = friendly_model_error(
            "ollama/llama3.3",
            Exception("Connection refused by localhost:11434"),
        )
        assert "Could not reach" in text and "ollama" in text

    def test_unrecognized_error_still_names_the_model(self):
        text = friendly_model_error("google/gemini-x", Exception("weird failure"))
        assert "google/gemini-x" in text and "weird failure" in text

    def test_status_code_from_response_attribute(self):
        class _Response:
            status_code = 429

        class _SdkError(Exception):
            response = _Response()

        text = friendly_model_error("v/m", _SdkError("boom"))
        assert "rate-limited" in text


class TestWrapProviderErrors:
    def test_wraps_sdk_exception(self):
        with pytest.raises(ModelProviderError) as excinfo:
            with wrap_provider_errors("openai/gpt-4o"):
                raise _StatusError("bad key", 401)
        assert excinfo.value.model == "openai/gpt-4o"
        assert "Authentication" in str(excinfo.value.message)
        assert isinstance(excinfo.value.inner_exception, _StatusError)
        assert isinstance(excinfo.value.__cause__, _StatusError)

    def test_flux_control_flow_passes_through(self):
        with pytest.raises(ExecutionError) as excinfo:
            with wrap_provider_errors("openai/gpt-4o"):
                raise ExecutionError(message="already wrapped")
        assert not isinstance(excinfo.value, ModelProviderError)

    def test_no_error_no_effect(self):
        with wrap_provider_errors("openai/gpt-4o"):
            value = 42
        assert value == 42
