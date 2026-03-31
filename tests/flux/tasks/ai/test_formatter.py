import pytest
from flux.tasks.ai.formatter import LLMFormatter


def test_formatter_cannot_be_instantiated():
    with pytest.raises(TypeError):
        LLMFormatter()


def test_formatter_defines_required_methods():
    assert hasattr(LLMFormatter, "build_messages")
    assert hasattr(LLMFormatter, "format_assistant_message")
    assert hasattr(LLMFormatter, "format_tool_results")
    assert hasattr(LLMFormatter, "format_user_message")
    assert hasattr(LLMFormatter, "remove_tools_from_kwargs")
    assert hasattr(LLMFormatter, "stream")
