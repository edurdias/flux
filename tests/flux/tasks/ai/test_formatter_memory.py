from __future__ import annotations

import json


def _wm_messages_with_tools():
    return [
        {"role": "user", "content": "find TODOs"},
        {
            "role": "tool_call",
            "content": json.dumps(
                {"calls": [{"id": "c1", "name": "grep", "arguments": {"pattern": "TODO"}}]},
            ),
        },
        {
            "role": "tool_result",
            "content": json.dumps(
                {"call_id": "c1", "name": "grep", "output": "file.py:10: TODO fix"},
            ),
        },
        {"role": "assistant", "content": "Found 1 TODO"},
        {"role": "user", "content": "fix it"},
    ]


class TestAnthropicFormatterMemory:
    def test_build_messages_reconstitutes_tool_roles(self):
        from unittest.mock import MagicMock
        from flux.tasks.ai.anthropic import AnthropicFormatter

        formatter = AnthropicFormatter(MagicMock(), "claude-test", 4096)
        wm = MagicMock()
        wm.recall.return_value = _wm_messages_with_tools()

        messages, _ = formatter.build_messages("system", "new q", wm)

        assistant_msgs = [m for m in messages if m.get("role") == "assistant"]
        has_tool_use = any(
            isinstance(m.get("content"), list)
            and any(b.get("type") == "tool_use" for b in m["content"])
            for m in assistant_msgs
        )
        assert has_tool_use

        user_msgs = [m for m in messages if m.get("role") == "user"]
        has_tool_result = any(
            isinstance(m.get("content"), list)
            and any(b.get("type") == "tool_result" for b in m["content"])
            for m in user_msgs
        )
        assert has_tool_result


class TestOpenAIFormatterMemory:
    def test_build_messages_reconstitutes_tool_roles(self):
        from unittest.mock import MagicMock
        from flux.tasks.ai.openai import OpenAIFormatter

        formatter = OpenAIFormatter(MagicMock(), "gpt-test")
        wm = MagicMock()
        wm.recall.return_value = _wm_messages_with_tools()

        messages, _ = formatter.build_messages("system", "new q", wm)

        roles = [m.get("role") for m in messages]
        assert "tool" in roles
        has_tool_calls = any("tool_calls" in m for m in messages if m.get("role") == "assistant")
        assert has_tool_calls


class TestOllamaFormatterMemory:
    def test_build_messages_reconstitutes_tool_roles(self):
        from flux.tasks.ai.ollama import OllamaFormatter

        formatter = OllamaFormatter(None, "test-model")
        wm = type("FakeWM", (), {"recall": lambda self: _wm_messages_with_tools()})()

        messages, _ = formatter.build_messages("system", "new q", wm)

        roles = [m.get("role") for m in messages]
        assert "tool" in roles
        has_tool_calls = any("tool_calls" in m for m in messages if m.get("role") == "assistant")
        assert has_tool_calls
