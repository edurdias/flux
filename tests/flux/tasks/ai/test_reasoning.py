from __future__ import annotations

import pytest

from flux.domain.execution_context import ExecutionContext
from flux.task import task
from flux.tasks.ai.models import LLMResponse, ReasoningContent, ToolCall


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
            {"role": "reasoning", "content": '{"text": "hmm", "opaque": null}'},
            {"role": "assistant", "content": "hello"},
        ]
        converted = formatter._convert_memory_messages(messages)
        roles = [m["role"] for m in converted]
        assert "reasoning" not in roles
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
                "role": "reasoning",
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


class TestOpenAIReasoning:
    def test_to_llm_response_captures_reasoning_content(self):
        from unittest.mock import MagicMock

        from flux.tasks.ai.openai import _to_llm_response

        message = MagicMock()
        message.content = "The answer."
        message.tool_calls = None
        message.reasoning_content = "I reasoned step by step..."

        response = MagicMock()
        response.choices = [MagicMock(message=message)]

        result = _to_llm_response(response)
        assert result.reasoning is not None
        assert result.reasoning.text == "I reasoned step by step..."
        assert result.reasoning.opaque == {"reasoning_content": "I reasoned step by step..."}

    def test_to_llm_response_no_reasoning(self):
        from unittest.mock import MagicMock

        from flux.tasks.ai.openai import _to_llm_response

        message = MagicMock()
        message.content = "Hello"
        message.tool_calls = None
        message.reasoning_content = None

        response = MagicMock()
        response.choices = [MagicMock(message=message)]

        result = _to_llm_response(response)
        assert result.reasoning is None

    def test_format_assistant_message_includes_reasoning(self):
        from unittest.mock import MagicMock

        from flux.tasks.ai.openai import OpenAIFormatter

        formatter = OpenAIFormatter(MagicMock(), "gpt-test")
        response = LLMResponse(
            text="answer",
            reasoning=ReasoningContent(
                text="step by step",
                opaque={"reasoning_content": "step by step"},
            ),
        )
        msg = formatter.format_assistant_message(response)
        assert msg.get("reasoning_content") == "step by step"

    def test_convert_memory_handles_thinking_role(self):
        import json
        from unittest.mock import MagicMock

        from flux.tasks.ai.openai import OpenAIFormatter

        formatter = OpenAIFormatter(MagicMock(), "gpt-test")
        messages = [
            {"role": "user", "content": "question"},
            {
                "role": "reasoning",
                "content": json.dumps(
                    {
                        "text": "reasoning",
                        "opaque": {"reasoning_content": "reasoning"},
                    },
                ),
            },
            {"role": "assistant", "content": "answer"},
        ]
        converted = formatter._convert_memory_messages(messages)
        assistant_msgs = [m for m in converted if m.get("role") == "assistant"]
        assert any(m.get("reasoning_content") for m in assistant_msgs)

    def test_reasoning_effort_in_call_kwargs(self):
        from unittest.mock import MagicMock

        from flux.tasks.ai.openai import OpenAIFormatter

        formatter = OpenAIFormatter(MagicMock(), "gpt-test", reasoning_effort="medium")
        wm = type("FakeWM", (), {"recall": lambda self: []})()
        _, kwargs = formatter.build_messages("system", "question", wm)
        assert kwargs.get("reasoning_effort") == "medium"


class TestGeminiReasoning:
    def test_to_llm_response_captures_thought_parts(self):
        pytest.importorskip("google.genai")
        from unittest.mock import MagicMock

        from flux.tasks.ai.gemini import _to_llm_response

        thought_part = MagicMock()
        thought_part.thought = True
        thought_part.text = "Let me think..."
        thought_part.function_call = None

        text_part = MagicMock()
        text_part.thought = False
        text_part.text = "The answer."
        text_part.function_call = None

        candidate = MagicMock()
        candidate.content.parts = [thought_part, text_part]

        response = MagicMock()
        response.candidates = [candidate]
        response.text = "The answer."
        response.function_calls = None

        result = _to_llm_response(response)
        assert result.reasoning is not None
        assert result.reasoning.text == "Let me think..."
        assert result.reasoning.opaque == {"text": "Let me think...", "thought": True}

    def test_to_llm_response_no_thought(self):
        pytest.importorskip("google.genai")
        from unittest.mock import MagicMock

        from flux.tasks.ai.gemini import _to_llm_response

        text_part = MagicMock()
        text_part.thought = False
        text_part.text = "Hello"
        text_part.function_call = None

        candidate = MagicMock()
        candidate.content.parts = [text_part]

        response = MagicMock()
        response.candidates = [candidate]
        response.text = "Hello"
        response.function_calls = None

        result = _to_llm_response(response)
        assert result.reasoning is None

    def test_format_assistant_message_includes_thought(self):
        pytest.importorskip("google.genai")

        from flux.tasks.ai.gemini import GeminiFormatter

        formatter = GeminiFormatter("gemini-test", max_tokens=4096)
        response = LLMResponse(
            text="answer",
            reasoning=ReasoningContent(
                text="thinking...",
                opaque={"text": "thinking...", "thought": True},
            ),
        )
        content = formatter.format_assistant_message(response)
        parts = content.parts
        has_thought = any(getattr(p, "thought", False) for p in parts)
        assert has_thought

    def test_format_assistant_message_omits_when_none(self):
        pytest.importorskip("google.genai")
        from flux.tasks.ai.gemini import GeminiFormatter

        formatter = GeminiFormatter("gemini-test", max_tokens=4096)
        response = LLMResponse(text="answer")
        content = formatter.format_assistant_message(response)
        parts = content.parts
        has_thought = any(getattr(p, "thought", False) for p in parts)
        assert not has_thought


class TestAgentLoopReasoning:
    @pytest.mark.asyncio
    async def test_thinking_stored_in_working_memory(self):
        from flux.tasks.ai.agent_loop import run_agent_loop
        from flux.tasks.ai.memory.working_memory import WorkingMemory
        from flux.tasks.ai.tool_executor import build_tool_schemas

        call_count = 0

        @task
        async def fake_llm_reasoning(messages, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return LLMResponse(
                    text="",
                    tool_calls=[ToolCall(id="c1", name="my_tool", arguments={"x": 1})],
                    reasoning=ReasoningContent(text="Let me think...", opaque=None),
                )
            return LLMResponse(
                text="done",
                reasoning=ReasoningContent(text="Final thought", opaque=None),
            )

        @task
        async def my_tool(x: int) -> str:
            """Test tool."""
            return "result"

        class FakeFormatter:
            def build_messages(self, sys, user, wm):
                return [{"role": "user", "content": user}], {}

            def format_assistant_message(self, r):
                return {"role": "assistant", "content": r.text}

            def format_tool_results(self, tc, results):
                return [{"role": "tool", "content": r["output"]} for r in results]

            def format_user_message(self, text):
                return {"role": "user", "content": text}

            def remove_tools_from_kwargs(self, kw):
                return kw

            async def stream(self, messages, kwargs):
                yield "hello"

        tools = [my_tool]
        schemas = build_tool_schemas(tools)

        ctx = ExecutionContext(workflow_id="test", workflow_name="test")
        token = ExecutionContext.set(ctx)
        try:
            wm = WorkingMemory()
            await run_agent_loop(
                llm_task=fake_llm_reasoning,
                formatter=FakeFormatter(),
                system_prompt="test",
                instruction="test",
                tools=tools,
                tool_schemas=schemas,
                working_memory=wm,
                stream=False,
            )
            messages = wm.recall()
            roles = [m["role"] for m in messages]
            assert "reasoning" in roles
            thinking_msgs = [m for m in messages if m["role"] == "reasoning"]
            assert len(thinking_msgs) >= 1
        finally:
            ExecutionContext.reset(token)

    @pytest.mark.asyncio
    async def test_thinking_order_before_tool_call(self):
        from flux.tasks.ai.agent_loop import run_agent_loop
        from flux.tasks.ai.memory.working_memory import WorkingMemory
        from flux.tasks.ai.tool_executor import build_tool_schemas

        call_count = 0

        @task
        async def fake_llm(messages, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return LLMResponse(
                    text="",
                    tool_calls=[ToolCall(id="c1", name="my_tool", arguments={"x": 1})],
                    reasoning=ReasoningContent(text="thinking first", opaque=None),
                )
            return LLMResponse(text="done")

        @task
        async def my_tool(x: int) -> str:
            """Test tool."""
            return "ok"

        class FakeFormatter:
            def build_messages(self, sys, user, wm):
                return [{"role": "user", "content": user}], {}

            def format_assistant_message(self, r):
                return {"role": "assistant", "content": r.text}

            def format_tool_results(self, tc, results):
                return [{"role": "tool", "content": r["output"]} for r in results]

            def format_user_message(self, text):
                return {"role": "user", "content": text}

            def remove_tools_from_kwargs(self, kw):
                return kw

        tools = [my_tool]
        schemas = build_tool_schemas(tools)

        ctx = ExecutionContext(workflow_id="test", workflow_name="test")
        token = ExecutionContext.set(ctx)
        try:
            wm = WorkingMemory()
            await run_agent_loop(
                llm_task=fake_llm,
                formatter=FakeFormatter(),
                system_prompt="test",
                instruction="test",
                tools=tools,
                tool_schemas=schemas,
                working_memory=wm,
                stream=False,
            )
            messages = wm.recall()
            roles = [m["role"] for m in messages]
            thinking_idx = roles.index("reasoning")
            tool_call_idx = roles.index("tool_call")
            assert thinking_idx < tool_call_idx
        finally:
            ExecutionContext.reset(token)
