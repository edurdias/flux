from flux.tasks.ai.models import LLMResponse, ToolCall


def test_tool_call_creation():
    tc = ToolCall(id="call_1", name="search", arguments={"q": "test"})
    assert tc.id == "call_1"
    assert tc.name == "search"
    assert tc.arguments == {"q": "test"}


def test_tool_call_serialization():
    tc = ToolCall(id="call_1", name="search", arguments={"q": "test"})
    d = tc.model_dump()
    assert d == {"id": "call_1", "name": "search", "arguments": {"q": "test"}}


def test_llm_response_text_only():
    r = LLMResponse(text="Hello world")
    assert r.text == "Hello world"
    assert r.tool_calls == []


def test_llm_response_tool_calls_only():
    r = LLMResponse(tool_calls=[
        ToolCall(id="1", name="search", arguments={"q": "AI"}),
    ])
    assert r.text == ""
    assert len(r.tool_calls) == 1


def test_llm_response_mixed():
    r = LLMResponse(
        text="Let me search.",
        tool_calls=[ToolCall(id="1", name="search", arguments={"q": "AI"})],
    )
    assert r.text == "Let me search."
    assert len(r.tool_calls) == 1


def test_llm_response_serialization_roundtrip():
    r = LLMResponse(
        text="Done",
        tool_calls=[ToolCall(id="1", name="search", arguments={"q": "AI"})],
    )
    d = r.model_dump()
    r2 = LLMResponse.model_validate(d)
    assert r2.text == r.text
    assert r2.tool_calls[0].id == r.tool_calls[0].id


def test_llm_response_defaults():
    r = LLMResponse()
    assert r.text == ""
    assert r.tool_calls == []


def test_llm_response_has_tool_calls():
    empty = LLMResponse(text="hi")
    with_tools = LLMResponse(tool_calls=[ToolCall(id="1", name="x", arguments={})])
    assert not empty.tool_calls
    assert with_tools.tool_calls
