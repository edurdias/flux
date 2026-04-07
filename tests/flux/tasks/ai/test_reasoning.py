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


class TestAnthropicReasoning:
    def test_to_llm_response_captures_thinking(self):
        from unittest.mock import MagicMock

        from flux.tasks.ai.anthropic import _to_llm_response

        thinking_block = MagicMock()
        thinking_block.type = "thinking"
        thinking_block.thinking = "Let me reason..."
        thinking_block.signature = "sig_abc"

        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = "The answer."

        response = MagicMock()
        response.content = [thinking_block, text_block]

        result = _to_llm_response(response)
        assert result.reasoning is not None
        assert result.reasoning.text == "Let me reason..."
        assert result.reasoning.opaque["type"] == "thinking"
        assert result.reasoning.opaque["signature"] == "sig_abc"

    def test_to_llm_response_no_thinking(self):
        from unittest.mock import MagicMock

        from flux.tasks.ai.anthropic import _to_llm_response

        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = "Hello"

        response = MagicMock()
        response.content = [text_block]

        result = _to_llm_response(response)
        assert result.reasoning is None

    def test_format_assistant_message_includes_thinking(self):
        from unittest.mock import MagicMock

        from flux.tasks.ai.anthropic import AnthropicFormatter

        formatter = AnthropicFormatter(MagicMock(), "claude-test", 4096)
        response = LLMResponse(
            text="answer",
            reasoning=ReasoningContent(
                text="thinking...",
                opaque={"type": "thinking", "thinking": "thinking...", "signature": "sig"},
            ),
        )
        msg = formatter.format_assistant_message(response)
        assert msg["role"] == "assistant"
        content = msg["content"]
        assert isinstance(content, list)
        assert content[0]["type"] == "thinking"
        assert content[0]["signature"] == "sig"

    def test_format_assistant_message_omits_thinking_when_none(self):
        from unittest.mock import MagicMock

        from flux.tasks.ai.anthropic import AnthropicFormatter

        formatter = AnthropicFormatter(MagicMock(), "claude-test", 4096)
        response = LLMResponse(text="answer")
        msg = formatter.format_assistant_message(response)
        content = msg["content"]
        has_thinking = any(isinstance(c, dict) and c.get("type") == "thinking" for c in content)
        assert not has_thinking

    def test_convert_memory_merges_thinking_with_next_message(self):
        import json
        from unittest.mock import MagicMock

        from flux.tasks.ai.anthropic import AnthropicFormatter

        formatter = AnthropicFormatter(MagicMock(), "claude-test", 4096)
        messages = [
            {"role": "user", "content": "question"},
            {
                "role": "thinking",
                "content": json.dumps(
                    {
                        "text": "reasoning...",
                        "opaque": {
                            "type": "thinking",
                            "thinking": "reasoning...",
                            "signature": "sig",
                        },
                    },
                ),
            },
            {"role": "assistant", "content": "answer"},
        ]
        converted = formatter._convert_memory_messages(messages)
        assert len(converted) == 2
        assistant_msg = converted[1]
        assert assistant_msg["role"] == "assistant"
        content = assistant_msg["content"]
        assert isinstance(content, list)
        assert content[0]["type"] == "thinking"

    def test_reasoning_effort_adds_thinking_config(self):
        from unittest.mock import MagicMock

        from flux.tasks.ai.anthropic import AnthropicFormatter

        formatter = AnthropicFormatter(MagicMock(), "claude-test", 4096, reasoning_effort="medium")
        wm = type("FakeWM", (), {"recall": lambda self: []})()
        _, kwargs = formatter.build_messages("system", "question", wm)
        assert "thinking" in kwargs
        assert kwargs["thinking"]["type"] == "adaptive"
