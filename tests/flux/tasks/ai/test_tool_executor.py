from __future__ import annotations


from flux import task
from flux.tasks.ai.tool_executor import (
    build_tool_schemas,
    execute_tools,
    extract_tool_calls_from_content,
    strip_tool_calls_from_content,
)


@task
async def search_web(query: str) -> str:
    """Search the web and return relevant results."""
    return f"Results for: {query}"


@task
async def get_temperature(city: str, unit: str = "celsius") -> str:
    """Get the current temperature for a city."""
    return f"22 {unit} in {city}"


@task.with_options(secret_requests=["API_KEY"])
async def secret_tool(query: str, *, secrets) -> str:
    """A tool that requires secrets."""
    return "secret result"


def test_build_tool_schemas_basic():
    schemas = build_tool_schemas([search_web])
    assert len(schemas) == 1
    s = schemas[0]
    assert s["name"] == "search_web"
    assert s["description"] == "Search the web and return relevant results."
    assert "query" in s["parameters"]["properties"]
    assert s["parameters"]["properties"]["query"]["type"] == "string"
    assert "query" in s["parameters"]["required"]


def test_build_tool_schemas_with_defaults():
    schemas = build_tool_schemas([get_temperature])
    s = schemas[0]
    assert "city" in s["parameters"]["required"]
    assert "unit" not in s["parameters"]["required"]


def test_build_tool_schemas_excludes_secrets():
    schemas = build_tool_schemas([secret_tool])
    s = schemas[0]
    assert "secrets" not in s["parameters"]["properties"]


def test_build_tool_schemas_multiple_tools():
    schemas = build_tool_schemas([search_web, get_temperature])
    assert len(schemas) == 2
    names = {s["name"] for s in schemas}
    assert names == {"search_web", "get_temperature"}


def test_execute_tools_basic():
    from flux import ExecutionContext, workflow

    @workflow
    async def test_wf(ctx: ExecutionContext):
        results = await execute_tools(
            [{"id": "1", "name": "search_web", "arguments": {"query": "test"}}],
            [search_web],
        )
        return results

    ctx = test_wf.run()
    assert ctx.has_succeeded
    assert len(ctx.output) == 1
    assert ctx.output[0]["output"] == "Results for: test"


def test_execute_tools_unknown_tool():
    from flux import ExecutionContext, workflow

    @workflow
    async def test_wf(ctx: ExecutionContext):
        results = await execute_tools(
            [{"id": "1", "name": "nonexistent", "arguments": {}}],
            [search_web],
        )
        return results

    ctx = test_wf.run()
    assert ctx.has_succeeded
    assert "Unknown tool" in ctx.output[0]["output"]


def test_execute_tools_dict_result_serialized_as_json():
    from flux import ExecutionContext, workflow

    @task
    async def dict_tool() -> dict:
        """Returns a dict."""
        return {"key": "value", "nested": [1, 2]}

    @workflow
    async def test_wf(ctx: ExecutionContext):
        results = await execute_tools(
            [{"id": "call_1", "name": "dict_tool", "arguments": {}}],
            [dict_tool],
        )
        return results

    ctx = test_wf.run()
    assert ctx.has_succeeded
    result = ctx.output[0]
    assert result["output"] == '{"key": "value", "nested": [1, 2]}'


def test_execute_tools_list_result_serialized_as_json():
    from flux import ExecutionContext, workflow

    @task
    async def list_tool() -> list:
        """Returns a list."""
        return [{"a": 1}, {"b": 2}]

    @workflow
    async def test_wf(ctx: ExecutionContext):
        results = await execute_tools(
            [{"id": "call_1", "name": "list_tool", "arguments": {}}],
            [list_tool],
        )
        return results

    ctx = test_wf.run()
    assert ctx.has_succeeded
    result = ctx.output[0]
    assert result["output"] == '[{"a": 1}, {"b": 2}]'


def test_execute_tools_string_result_unchanged():
    from flux import ExecutionContext, workflow

    @task
    async def string_tool() -> str:
        """Returns a string."""
        return "hello world"

    @workflow
    async def test_wf(ctx: ExecutionContext):
        results = await execute_tools(
            [{"id": "call_1", "name": "string_tool", "arguments": {}}],
            [string_tool],
        )
        return results

    ctx = test_wf.run()
    assert ctx.has_succeeded
    result = ctx.output[0]
    assert result["output"] == "hello world"


# --- extract_tool_calls_from_content tests ---


def test_extract_mistral_format():
    content = '[TOOL_CALLS] [{"name": "search_web", "arguments": {"query": "AI"}}]'
    result = extract_tool_calls_from_content(content, {"search_web"})
    assert result is not None
    assert len(result) == 1
    assert result[0]["name"] == "search_web"
    assert result[0]["arguments"] == {"query": "AI"}


def test_extract_qwen_format():
    content = '<tool_call>{"name": "get_weather", "arguments": {"city": "Paris"}}</tool_call>'
    result = extract_tool_calls_from_content(content, {"get_weather"})
    assert result is not None
    assert len(result) == 1
    assert result[0]["name"] == "get_weather"


def test_extract_json_array_format():
    content = '[{"name": "search_web", "arguments": {"query": "test"}}]'
    result = extract_tool_calls_from_content(content, {"search_web"})
    assert result is not None
    assert len(result) == 1


def test_extract_multiple_tool_calls():
    content = (
        '[TOOL_CALLS] [{"name": "search_web", "arguments": {"query": "AI"}}, '
        '{"name": "analyze_data", "arguments": {"data": "results"}}]'
    )
    result = extract_tool_calls_from_content(content, {"search_web", "analyze_data"})
    assert result is not None
    assert len(result) == 2


def test_extract_ignores_unknown_tools():
    content = '[TOOL_CALLS] [{"name": "unknown_tool", "arguments": {}}]'
    result = extract_tool_calls_from_content(content, {"search_web"})
    assert result is None


def test_extract_returns_none_for_plain_text():
    content = "The weather in Paris is sunny today."
    result = extract_tool_calls_from_content(content, {"get_weather"})
    assert result is None


def test_extract_returns_none_for_empty():
    assert extract_tool_calls_from_content("", {"search_web"}) is None
    assert extract_tool_calls_from_content(None, {"search_web"}) is None


def test_extract_returns_none_for_no_tools():
    content = '[TOOL_CALLS] [{"name": "search_web", "arguments": {}}]'
    assert extract_tool_calls_from_content(content, set()) is None


def test_extract_handles_parameters_key():
    content = '[TOOL_CALLS] [{"name": "search_web", "parameters": {"query": "AI"}}]'
    result = extract_tool_calls_from_content(content, {"search_web"})
    assert result is not None
    assert result[0]["arguments"] == {"query": "AI"}


# --- strip_tool_calls_from_content tests ---


def test_strip_mistral_format():
    content = '[TOOL_CALLS] [{"name": "search_web", "arguments": {"query": "AI"}}]'
    assert strip_tool_calls_from_content(content) == ""


def test_strip_with_preceding_text():
    content = 'Here is the report.[TOOL_CALLS][{"name": "mark_step_done", "arguments": {}}]'
    assert strip_tool_calls_from_content(content) == "Here is the report."


def test_strip_qwen_format():
    content = 'Result: <tool_call>{"name": "x", "arguments": {}}</tool_call>'
    assert strip_tool_calls_from_content(content) == "Result:"


def test_strip_plain_text_unchanged():
    content = "The weather in Paris is sunny."
    assert strip_tool_calls_from_content(content) == content


def test_strip_empty():
    assert strip_tool_calls_from_content("") == ""
    assert strip_tool_calls_from_content(None) is None
