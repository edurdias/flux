"""Friendly provider-error mapping (issue #141C).

``friendly_model_error(model, exc)`` turns the raw SDK exceptions every
provider throws — auth failures, missing models, rate limits, context
overflows, unreachable endpoints — into one line of actionable text with
the original error appended. ``wrap_provider_errors`` is the shared guard
the provider llm tasks run under: anything that escapes the provider call
is re-raised as :class:`flux.errors.ModelProviderError` carrying the
friendly text, with the SDK exception preserved as ``inner_exception`` and
``__cause__``.

Mapping is by shape, not by SDK type: status codes where present
(``status_code``/``http_status``/``response.status_code``), message
markers otherwise — so one mapper serves the OpenAI, Anthropic, Gemini,
and Ollama SDKs plus every OpenAI-compatible vendor.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

from flux.errors import ModelProviderError

_CONTEXT_MARKERS = (
    "context_length",
    "context length",
    "maximum context",
    "too many tokens",
    "prompt is too long",
    "input length",
)
_AUTH_MARKERS = (
    "api key",
    "api_key",
    "authentication",
    "unauthorized",
    "permission",
    "invalid x-api-key",
)
_NOT_FOUND_MARKERS = ("model_not_found", "model not found", "does not exist", "unknown model")
_RATE_MARKERS = ("rate limit", "rate_limit", "quota", "overloaded", "too many requests")
_CONNECT_MARKERS = (
    "connection refused",
    "connection error",
    "connect call failed",
    "name or service not known",
    "getaddrinfo",
    "timed out",
    "unreachable",
)


def _status_code(exc: Exception) -> int | None:
    for attr in ("status_code", "http_status", "code"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    response = getattr(exc, "response", None)
    value = getattr(response, "status_code", None)
    return value if isinstance(value, int) else None


def friendly_model_error(model: str, exc: Exception) -> str:
    """One actionable line for a provider failure, raw error preserved.

    Never raises: an unrecognized exception maps to a generic line that
    still names the model and keeps the original message.
    """
    provider = model.partition("/")[0]
    raw = f"{type(exc).__name__}: {exc}"
    lowered = str(exc).lower()
    status = _status_code(exc)

    if status in (401, 403) or any(marker in lowered for marker in _AUTH_MARKERS):
        return (
            f"Authentication with the '{provider}' provider failed for model "
            f"'{model}': the API key is missing, invalid, or lacks access. "
            f"Check the provider's key configuration. ({raw})"
        )
    if status == 404 or any(marker in lowered for marker in _NOT_FOUND_MARKERS):
        return (
            f"Model '{model}' is not available: the provider does not serve "
            f"this model id (or this account cannot access it). Check the "
            f"model name and your plan. ({raw})"
        )
    if status == 429 or any(marker in lowered for marker in _RATE_MARKERS):
        return (
            f"The '{provider}' provider rate-limited the call to '{model}'. "
            f"Retry later, lower concurrency, or raise your quota. ({raw})"
        )
    if any(marker in lowered for marker in _CONTEXT_MARKERS):
        return (
            f"The request to '{model}' exceeded the model's context window. "
            f"Shorten the prompt/history, trim tool output, or use a model "
            f"with a larger context. ({raw})"
        )
    if any(marker in lowered for marker in _CONNECT_MARKERS):
        return (
            f"Could not reach the '{provider}' provider endpoint for "
            f"'{model}'. Check that the service is running and the base URL "
            f"is correct. ({raw})"
        )
    return f"LLM call to '{model}' failed. ({raw})"


@contextlib.contextmanager
def wrap_provider_errors(model: str) -> Iterator[None]:
    """Re-raise provider-call failures as ModelProviderError.

    Flux's own control-flow exceptions pass through untouched — only
    genuine provider/SDK failures get wrapped.
    """
    from flux.errors import ExecutionError

    # CancelledError is BaseException and passes through untouched.
    try:
        yield
    except ExecutionError:
        raise
    except Exception as exc:
        raise ModelProviderError(model, friendly_model_error(model, exc), exc) from exc
