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


class TestOllamaReasoning:
    def test_to_llm_response_captures_thinking(self):
        from flux.tasks.ai.ollama import _to_llm_response

        response = {
            "message": {
                "content": "The answer is 42.",
                "thinking": "Let me think step by step...",
            },
        }
        result = _to_llm_response(response)
        assert result.reasoning is not None
        assert result.reasoning.text == "Let me think step by step..."
        assert result.reasoning.opaque is None

    def test_to_llm_response_no_thinking(self):
        from flux.tasks.ai.ollama import _to_llm_response

        response = {"message": {"content": "Hello"}}
        result = _to_llm_response(response)
        assert result.reasoning is None

    def test_format_assistant_message_no_reasoning_injection(self):
        from flux.tasks.ai.ollama import OllamaFormatter

        formatter = OllamaFormatter("test-model")
        response = LLMResponse(
            text="answer",
            reasoning=ReasoningContent(text="thinking"),
        )
        msg = formatter.format_assistant_message(response)
        assert msg["role"] == "assistant"
        assert msg["content"] == "answer"
        assert "thinking" not in msg

    def test_convert_memory_skips_thinking_role(self):
        from flux.tasks.ai.ollama import OllamaFormatter

        formatter = OllamaFormatter("test-model")
        messages = [
            {"role": "user", "content": "hi"},
            {"role": "thinking", "content": '{"text": "hmm", "opaque": null}'},
            {"role": "assistant", "content": "hello"},
        ]
        converted = formatter._convert_memory_messages(messages)
        roles = [m["role"] for m in converted]
        assert "thinking" not in roles
        assert len(converted) == 2

    def test_reasoning_effort_adds_think_param(self):
        from flux.tasks.ai.ollama import OllamaFormatter

        formatter = OllamaFormatter("test-model", reasoning_effort="high")
        wm = type("FakeWM", (), {"recall": lambda self: []})()
        _, kwargs = formatter.build_messages("system", "question", wm)
        assert kwargs.get("think") is True

    def test_reasoning_effort_none_no_think_param(self):
        from flux.tasks.ai.ollama import OllamaFormatter

        formatter = OllamaFormatter("test-model")
        wm = type("FakeWM", (), {"recall": lambda self: []})()
        _, kwargs = formatter.build_messages("system", "question", wm)
        assert "think" not in kwargs
