from __future__ import annotations


from flux.tasks.ai.models import LLMResponse, ReasoningContent


class TestReasoningContent:
    def test_defaults_to_none(self):
        rc = ReasoningContent()
        assert rc.text is None
        assert rc.opaque is None

    def test_with_text_only(self):
        rc = ReasoningContent(text="thinking about it")
        assert rc.text == "thinking about it"
        assert rc.opaque is None

    def test_with_text_and_opaque(self):
        rc = ReasoningContent(
            text="step by step",
            opaque={"signature": "abc123"},
        )
        assert rc.text == "step by step"
        assert rc.opaque == {"signature": "abc123"}


class TestLLMResponseReasoning:
    def test_reasoning_defaults_to_none(self):
        r = LLMResponse(text="hello")
        assert r.reasoning is None

    def test_reasoning_with_content(self):
        r = LLMResponse(
            text="answer",
            reasoning=ReasoningContent(text="I thought about it"),
        )
        assert r.reasoning is not None
        assert r.reasoning.text == "I thought about it"

    def test_backward_compatible(self):
        r = LLMResponse(text="hello", tool_calls=[])
        assert r.text == "hello"
        assert r.tool_calls == []
        assert r.reasoning is None
