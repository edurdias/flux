"""OpenAI-compatible provider descriptors (issue #141B).

Every vendor speaking the OpenAI chat-completions wire format — DeepSeek,
Together, Fireworks, Groq, Mistral, xAI, a local vLLM — differs only by
``base_url``, auth, and model ids. A :class:`ProviderDescriptor` captures
exactly that, so adding a vendor is a config table row
(``[flux.ai.providers.<name>]``) or one ``register_provider`` call, not a
new provider module. Several descriptors coexist in one process, each
addressed by its own prefix in ``agent(model="<name>/<model>")`` — unlike
the ``OPENAI_BASE_URL`` env-var escape hatch, which is process-global,
single-valued, and silently changes the meaning of every ``openai/...``
model string.

Resolution never mistakes a version tag for structure: the provider prefix
is whatever precedes the first ``/`` in the model string, and only a prefix
that resolves to a *registered* descriptor (or a built-in provider) is
treated as one — ``qwen2.5-coder:32b`` stays a model name.

Clients are cached per descriptor and invalidated with ``invalidate()`` so
a config or key change is picked up without rebuilding every agent.
"""

from __future__ import annotations

import os
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

from flux.errors import ModelProviderError

# The four hand-written providers keep their modules; a descriptor cannot
# shadow them (native Anthropic/Gemini clients are not replaceable by a
# compat shim, and silently rerouting openai/ollama would be a trap).
BUILTIN_PROVIDERS = ("ollama", "openai", "anthropic", "google")


class ProviderDescriptor(BaseModel):
    """One OpenAI-compatible vendor: name, endpoint, key reference, models."""

    model_config = ConfigDict(frozen=True)

    name: str
    base_url: str
    api_key_env: str | None = None
    api_key_secret: str | None = None
    # Informational: the model ids this provider is known to serve.
    models: tuple[str, ...] = ()

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        if not value or "/" in value or ":" in value:
            raise ValueError(
                f"provider name must be a bare prefix (no '/' or ':'), got: '{value}'",
            )
        if value in BUILTIN_PROVIDERS:
            raise ValueError(
                f"provider name '{value}' is a built-in provider and cannot be redefined",
            )
        return value

    @field_validator("base_url")
    @classmethod
    def _validate_base_url(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError(f"base_url must be an http(s) URL, got: '{value}'")
        return value


# Programmatic registrations; config-declared descriptors are read live from
# Configuration so `Configuration.get().override(...)` is honored without a
# separate reload step.
_registered: dict[str, ProviderDescriptor] = {}
# AsyncOpenAI clients keyed by (name, base_url, key fingerprint).
_clients: dict[tuple[str, str, str], Any] = {}


def register_provider(descriptor: ProviderDescriptor) -> None:
    """Register (or replace) a descriptor programmatically.

    Programmatic registrations take precedence over config-declared ones of
    the same name.
    """
    _registered[descriptor.name] = descriptor
    invalidate(descriptor.name)


def invalidate(name: str | None = None) -> None:
    """Drop cached clients (for one provider, or all) so the next call
    re-reads config and keys."""
    if name is None:
        _clients.clear()
        return
    for key in [k for k in _clients if k[0] == name]:
        _clients.pop(key, None)


def get_provider_descriptor(name: str) -> ProviderDescriptor | None:
    """Resolve a provider prefix to its descriptor, or None.

    Programmatic registrations win; otherwise the live configuration's
    ``[flux.ai.providers]`` table is consulted.
    """
    if name in _registered:
        return _registered[name]
    from flux.config import Configuration

    configured = Configuration.get().settings.ai.providers.get(name)
    if configured is None:
        return None
    try:
        return ProviderDescriptor(
            name=name,
            base_url=configured.base_url,
            api_key_env=configured.api_key_env,
            api_key_secret=configured.api_key_secret,
            models=tuple(configured.models or ()),
        )
    except ValueError as e:
        raise ModelProviderError(
            f"{name}/",
            f"Invalid [flux.ai.providers.{name}] configuration: {e}",
            e,
        ) from e


def registered_provider_names() -> list[str]:
    """Every prefix agent() will currently resolve through a descriptor."""
    from flux.config import Configuration

    names = set(_registered)
    names.update(Configuration.get().settings.ai.providers.keys())
    return sorted(names)


async def _resolve_api_key(descriptor: ProviderDescriptor) -> str:
    """API key for a descriptor: env var first, then the Flux secret store.

    Returns a placeholder for keyless endpoints (local vLLM and friends) —
    the OpenAI SDK requires *some* key string.
    """
    if descriptor.api_key_env:
        value = os.environ.get(descriptor.api_key_env)
        if value:
            return value
    if descriptor.api_key_secret:
        from flux.secret_managers import SecretManager

        secrets = await SecretManager.current().get([descriptor.api_key_secret])
        value = secrets.get(descriptor.api_key_secret)
        if value:
            return str(value)
    if descriptor.api_key_env or descriptor.api_key_secret:
        sources = [
            s
            for s in (
                f"environment variable '{descriptor.api_key_env}'"
                if descriptor.api_key_env
                else None,
                f"secret '{descriptor.api_key_secret}'" if descriptor.api_key_secret else None,
            )
            if s
        ]
        raise ModelProviderError(
            f"{descriptor.name}/",
            f"No API key found for provider '{descriptor.name}': set {' or '.join(sources)}.",
        )
    return "flux-no-key"


async def _get_client(descriptor: ProviderDescriptor) -> Any:
    try:
        from openai import AsyncOpenAI
    except ImportError:
        raise ImportError(
            f"Provider '{descriptor.name}' speaks the OpenAI-compatible "
            "protocol and needs the openai package: pip install openai",
        ) from None

    api_key = await _resolve_api_key(descriptor)
    cache_key = (descriptor.name, descriptor.base_url, api_key[:8])
    client = _clients.get(cache_key)
    if client is None:
        client = AsyncOpenAI(base_url=descriptor.base_url, api_key=api_key)
        _clients[cache_key] = client
    return client


async def build_openai_compatible_provider(
    descriptor: ProviderDescriptor,
    model_name: str,
    response_format: Any | None = None,
    reasoning_effort: str | None = None,
    max_tokens: int | None = None,
) -> tuple[Any, Any]:
    """(llm task, formatter) for a descriptor — the compat analog of
    ``build_openai_provider``, reusing ``OpenAIFormatter`` wholesale."""
    from flux.task import task
    from flux.tasks.ai.errors import wrap_provider_errors
    from flux.tasks.ai.openai import OpenAIFormatter, _to_llm_response

    client = await _get_client(descriptor)
    full_model = f"{descriptor.name}/{model_name}"

    @task.with_options(name=f"{descriptor.name}_llm")
    async def compat_llm(messages: list[dict], **kwargs: Any) -> Any:
        with wrap_provider_errors(full_model):
            response = await client.chat.completions.create(
                messages=messages,
                **kwargs,
            )
        return _to_llm_response(response)

    formatter = OpenAIFormatter(
        client,
        model_name,
        response_format,
        reasoning_effort,
        max_tokens,
    )
    return compat_llm, formatter
