"""Model capability resolution (issue #141, part A).

``resolve_capabilities(model)`` answers "what can this model do?" without a
network call, in two stages:

1. **Curated matrix** — keyed by the full model string exactly as
   ``agent(model=...)`` receives it (``provider/model_name``, tags and all).
   An exact hit wins outright.
2. **Conservative heuristics** — per-provider defaults for anything not in
   the matrix, so user-supplied model strings keep working at their own
   risk. Ollama in particular defaults ``parallel_tool_calls=False``: many
   local models accept the parallel-call wire format but mis-execute it.

Model names may contain colons (``ollama/qwen2.5-coder:32b``) — the
provider prefix is whatever precedes the *first* ``/``, and the rest is the
model name verbatim, so version tags are never mistaken for structure.

``register_model_capabilities`` lets deployments extend the matrix for
models Flux does not know about.
"""

from __future__ import annotations

from flux.tasks.ai.models import ModelCapabilities

_FULL = ModelCapabilities(
    tools=True,
    vision=True,
    pdf=True,
    parallel_tool_calls=True,
    streaming=True,
)
_OLLAMA_TOOLS = ModelCapabilities(tools=True, parallel_tool_calls=False)

# Keyed by the full string agent(model=...) receives. Keep entries factual —
# the heuristics below cover families; the matrix pins models whose behavior
# is known and stable.
CAPABILITY_MATRIX: dict[str, ModelCapabilities] = {
    # OpenAI — vision-capable tool callers; PDF ingestion is a file-input
    # feature outside the chat path Flux uses, so pdf stays False.
    "openai/gpt-4o": _FULL.model_copy(update={"pdf": False, "label": "GPT-4o"}),
    "openai/gpt-4o-mini": _FULL.model_copy(update={"pdf": False, "label": "GPT-4o mini"}),
    "openai/gpt-4.1": _FULL.model_copy(update={"pdf": False, "label": "GPT-4.1"}),
    "openai/gpt-4.1-mini": _FULL.model_copy(update={"pdf": False, "label": "GPT-4.1 mini"}),
    "openai/gpt-4.1-nano": _FULL.model_copy(update={"pdf": False, "label": "GPT-4.1 nano"}),
    # o1 rejects both parallel tool calls and streaming on the chat path.
    "openai/o1": ModelCapabilities(
        tools=True,
        vision=True,
        parallel_tool_calls=False,
        streaming=False,
        label="o1",
    ),
    # Anthropic — Claude 3.5+ handles images and PDFs natively.
    "anthropic/claude-sonnet-4-20250514": _FULL.model_copy(update={"label": "Claude Sonnet 4"}),
    "anthropic/claude-opus-4-20250514": _FULL.model_copy(update={"label": "Claude Opus 4"}),
    "anthropic/claude-sonnet-4-5": _FULL.model_copy(update={"label": "Claude Sonnet 4.5"}),
    "anthropic/claude-haiku-4-5": _FULL.model_copy(update={"label": "Claude Haiku 4.5"}),
    "anthropic/claude-3-7-sonnet-latest": _FULL.model_copy(update={"label": "Claude 3.7 Sonnet"}),
    # Google — Gemini is natively multimodal.
    "google/gemini-2.5-pro": _FULL.model_copy(update={"label": "Gemini 2.5 Pro"}),
    "google/gemini-2.5-flash": _FULL.model_copy(update={"label": "Gemini 2.5 Flash"}),
    "google/gemini-2.0-flash": _FULL.model_copy(update={"label": "Gemini 2.0 Flash"}),
    # Ollama — tool-capable families that mis-handle parallel calls; vision
    # models that cannot call tools at all.
    "ollama/llama3.1": _OLLAMA_TOOLS,
    "ollama/llama3.2": _OLLAMA_TOOLS,
    "ollama/llama3.3": _OLLAMA_TOOLS,
    "ollama/qwen2.5": _OLLAMA_TOOLS,
    "ollama/qwen3": _OLLAMA_TOOLS,
    "ollama/mistral": _OLLAMA_TOOLS,
    "ollama/llava": ModelCapabilities(tools=False, vision=True, parallel_tool_calls=False),
    "ollama/llama3.2-vision": ModelCapabilities(
        tools=False,
        vision=True,
        parallel_tool_calls=False,
    ),
}

# Ollama families with no tool-calling support: sending a tools payload gets
# a provider error (or silent misbehavior) rather than tool calls.
_OLLAMA_NO_TOOLS_MARKERS = ("llava", "-vision", "moondream")

# OpenAI families known vision-capable; anything else stays conservative.
_OPENAI_VISION_PREFIXES = ("gpt-4o", "gpt-4.1", "gpt-5")


def _heuristic_capabilities(provider: str, model_name: str) -> ModelCapabilities:
    lowered = model_name.lower()
    if provider == "ollama":
        no_tools = any(marker in lowered for marker in _OLLAMA_NO_TOOLS_MARKERS)
        return ModelCapabilities(
            tools=not no_tools,
            vision="vision" in lowered or "llava" in lowered,
            parallel_tool_calls=False,
        )
    if provider == "openai":
        # The o-series reasoning models historically mis-handle or reject
        # parallel tool calls; every mainline chat model supports them.
        o_series = lowered.startswith(("o1", "o3", "o4"))
        return ModelCapabilities(
            tools=True,
            vision=lowered.startswith(_OPENAI_VISION_PREFIXES),
            parallel_tool_calls=not o_series,
        )
    if provider == "anthropic":
        # Every Claude model the anthropic provider accepts is 3+: vision,
        # PDF, and parallel tool calls are table stakes.
        return _FULL
    if provider == "google":
        return _FULL
    # Unknown provider string: the most conservative record that still lets
    # a tool-calling agent try.
    return ModelCapabilities()


def resolve_capabilities(model: str) -> ModelCapabilities:
    """Capabilities for a full ``provider/model_name`` string.

    Exact matrix hit first, per-provider heuristics otherwise. Never raises
    and never touches the network — an unknown model resolves to a
    conservative record rather than an error.
    """
    hit = CAPABILITY_MATRIX.get(model)
    if hit is not None:
        return hit
    provider, sep, model_name = model.partition("/")
    if not sep:
        return ModelCapabilities()
    return _heuristic_capabilities(provider, model_name)


def register_model_capabilities(model: str, capabilities: ModelCapabilities) -> None:
    """Pin capabilities for a model the built-in matrix does not know.

    Deployment-level escape hatch: an exact entry always beats the
    heuristics, so this is also the way to *correct* a heuristic for a
    specific model string.
    """
    if not model or "/" not in model:
        raise ValueError(
            f"model must be a full 'provider/model_name' string, got: '{model}'",
        )
    CAPABILITY_MATRIX[model] = capabilities
