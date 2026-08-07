"""Model capability resolution (issue #141A): matrix hits, heuristic
fallbacks, colon-tagged model names, and the deployment escape hatch."""

from __future__ import annotations

import pytest

from flux.tasks.ai.capabilities import (
    CAPABILITY_MATRIX,
    register_model_capabilities,
    resolve_capabilities,
)
from flux.tasks.ai.models import ModelCapabilities


class TestMatrix:
    def test_exact_hit_wins(self):
        caps = resolve_capabilities("ollama/llama3.3")
        assert caps is CAPABILITY_MATRIX["ollama/llama3.3"]
        assert caps.tools is True
        assert caps.parallel_tool_calls is False

    def test_anthropic_models_are_fully_capable(self):
        caps = resolve_capabilities("anthropic/claude-sonnet-4-5")
        assert caps.tools and caps.vision and caps.pdf
        assert caps.parallel_tool_calls and caps.streaming

    def test_vision_only_ollama_model_has_no_tools(self):
        caps = resolve_capabilities("ollama/llava")
        assert caps.tools is False
        assert caps.vision is True

    def test_o1_disables_streaming_and_parallel_calls(self):
        caps = resolve_capabilities("openai/o1")
        assert caps.streaming is False
        assert caps.parallel_tool_calls is False
        assert caps.tools is True


class TestHeuristics:
    def test_unknown_ollama_model_defaults_conservative(self):
        caps = resolve_capabilities("ollama/some-finetune")
        assert caps.tools is True
        assert caps.parallel_tool_calls is False

    def test_colon_tag_is_model_name_not_structure(self):
        # qwen2.5-coder:32b — the colon is a version tag, not a provider
        # separator; resolution must not mangle it.
        caps = resolve_capabilities("ollama/qwen2.5-coder:32b")
        assert caps.tools is True
        assert caps.parallel_tool_calls is False

    def test_ollama_vision_family_detected(self):
        caps = resolve_capabilities("ollama/llama3.2-vision:11b")
        assert caps.vision is True
        assert caps.tools is False

    def test_unknown_openai_model_gets_parallel_calls(self):
        caps = resolve_capabilities("openai/gpt-99-turbo")
        assert caps.tools is True
        assert caps.parallel_tool_calls is True

    def test_openai_o_series_serializes(self):
        assert resolve_capabilities("openai/o4-mini").parallel_tool_calls is False

    def test_anthropic_and_google_heuristics_fully_capable(self):
        for model in ("anthropic/claude-future-9", "google/gemini-9.9-flash"):
            caps = resolve_capabilities(model)
            assert caps.vision and caps.pdf and caps.parallel_tool_calls

    def test_unknown_provider_is_conservative(self):
        caps = resolve_capabilities("acme/frontier-1")
        assert caps.tools is True
        assert caps.vision is False
        assert caps.parallel_tool_calls is False
        assert caps.streaming is True

    def test_no_slash_resolves_to_defaults_without_raising(self):
        caps = resolve_capabilities("just-a-model-name")
        assert caps == ModelCapabilities()


class TestRegistration:
    def test_registered_entry_beats_heuristics(self):
        model = "ollama/my-finetune:v3-test"
        try:
            register_model_capabilities(
                model,
                ModelCapabilities(tools=True, parallel_tool_calls=True),
            )
            assert resolve_capabilities(model).parallel_tool_calls is True
        finally:
            CAPABILITY_MATRIX.pop(model, None)

    def test_registration_requires_full_model_string(self):
        with pytest.raises(ValueError, match="provider/model_name"):
            register_model_capabilities("bare-name", ModelCapabilities())
        with pytest.raises(ValueError, match="provider/model_name"):
            register_model_capabilities("", ModelCapabilities())


class TestRecord:
    def test_record_is_frozen(self):
        caps = ModelCapabilities()
        with pytest.raises(Exception, match="frozen"):
            caps.tools = False

    def test_defaults_are_conservative(self):
        caps = ModelCapabilities()
        assert caps.tools is True
        assert caps.vision is False
        assert caps.pdf is False
        assert caps.parallel_tool_calls is False
        assert caps.streaming is True
        assert caps.label is None
