from __future__ import annotations

import asyncio
import time


from flux import task
from flux.tasks.ai.approval import requires_approval
from flux.tasks.ai.tool_executor import (
    build_tool_schemas,
    build_tools_preamble,
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


# --- parallel execute_tools tests ---


@task
async def slow_tool(label: str) -> str:
    """A tool that takes 0.3s."""
    await asyncio.sleep(0.3)
    return f"done:{label}"


@task
async def failing_tool() -> str:
    """A tool that raises."""
    raise ValueError("tool broke")


def test_execute_tools_parallel_runs_concurrently():
    from flux import ExecutionContext, workflow

    @workflow
    async def test_wf(ctx: ExecutionContext):
        calls = [
            {"id": "1", "name": "slow_tool", "arguments": {"label": "a"}},
            {"id": "2", "name": "slow_tool", "arguments": {"label": "b"}},
            {"id": "3", "name": "slow_tool", "arguments": {"label": "c"}},
        ]
        start = time.monotonic()
        results = await execute_tools(calls, [slow_tool])
        elapsed = time.monotonic() - start
        return {"results": results, "elapsed": elapsed}

    ctx = test_wf.run()
    assert ctx.has_succeeded
    assert len(ctx.output["results"]) == 3
    # 3 tools at 0.3s each: sequential = ~0.9s, parallel should be well under
    assert ctx.output["elapsed"] < 0.8


def test_execute_tools_parallel_preserves_order():
    from flux import ExecutionContext, workflow

    @workflow
    async def test_wf(ctx: ExecutionContext):
        calls = [
            {"id": "1", "name": "slow_tool", "arguments": {"label": "first"}},
            {"id": "2", "name": "slow_tool", "arguments": {"label": "second"}},
            {"id": "3", "name": "slow_tool", "arguments": {"label": "third"}},
        ]
        results = await execute_tools(calls, [slow_tool])
        return results

    ctx = test_wf.run()
    assert ctx.has_succeeded
    assert ctx.output[0]["output"] == "done:first"
    assert ctx.output[1]["output"] == "done:second"
    assert ctx.output[2]["output"] == "done:third"


def test_execute_tools_parallel_error_does_not_block_others():
    from flux import ExecutionContext, workflow

    @workflow
    async def test_wf(ctx: ExecutionContext):
        calls = [
            {"id": "1", "name": "slow_tool", "arguments": {"label": "ok"}},
            {"id": "2", "name": "failing_tool", "arguments": {}},
            {"id": "3", "name": "slow_tool", "arguments": {"label": "also_ok"}},
        ]
        results = await execute_tools(calls, [slow_tool, failing_tool])
        return results

    ctx = test_wf.run()
    assert ctx.has_succeeded
    assert ctx.output[0]["output"] == "done:ok"
    assert "Error:" in ctx.output[1]["output"]
    assert ctx.output[2]["output"] == "done:also_ok"


def test_execute_tools_max_concurrent_limits_parallelism():
    from flux import ExecutionContext, workflow

    @workflow
    async def test_wf(ctx: ExecutionContext):
        calls = [
            {"id": "1", "name": "slow_tool", "arguments": {"label": "a"}},
            {"id": "2", "name": "slow_tool", "arguments": {"label": "b"}},
            {"id": "3", "name": "slow_tool", "arguments": {"label": "c"}},
            {"id": "4", "name": "slow_tool", "arguments": {"label": "d"}},
        ]
        start = time.monotonic()
        results = await execute_tools(calls, [slow_tool], max_concurrent=2)
        elapsed = time.monotonic() - start
        return {"results": results, "elapsed": elapsed}

    ctx = test_wf.run()
    assert ctx.has_succeeded
    assert len(ctx.output["results"]) == 4
    # 4 tools, max 2 concurrent, 0.3s each: ~0.6s (2 batches)
    assert ctx.output["elapsed"] >= 0.5
    assert ctx.output["elapsed"] < 1.5


def test_execute_tools_max_concurrent_one_is_sequential():
    from flux import ExecutionContext, workflow

    @workflow
    async def test_wf(ctx: ExecutionContext):
        calls = [
            {"id": "1", "name": "slow_tool", "arguments": {"label": "a"}},
            {"id": "2", "name": "slow_tool", "arguments": {"label": "b"}},
            {"id": "3", "name": "slow_tool", "arguments": {"label": "c"}},
        ]
        start = time.monotonic()
        results = await execute_tools(calls, [slow_tool], max_concurrent=1)
        elapsed = time.monotonic() - start
        return {"results": results, "elapsed": elapsed}

    ctx = test_wf.run()
    assert ctx.has_succeeded
    assert len(ctx.output["results"]) == 3
    # Sequential: ~0.9s
    assert ctx.output["elapsed"] >= 0.7


def test_execute_tools_max_concurrent_zero_raises():
    from flux import ExecutionContext, workflow

    @workflow
    async def test_wf(ctx: ExecutionContext):
        return await execute_tools(
            [{"id": "1", "name": "search_web", "arguments": {"query": "x"}}],
            [search_web],
            max_concurrent=0,
        )

    ctx = test_wf.run()
    assert ctx.has_failed
    assert "max_concurrent must be >= 1" in str(ctx.output)


def test_execute_tools_single_call_unchanged():
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
    assert ctx.output[0]["output"] == "Results for: test"


# --- tool approval tests ---


@task
async def dangerous_tool(target: str) -> str:
    """Delete something dangerous."""
    return f"deleted:{target}"


dangerous_tool_approved = requires_approval(dangerous_tool)


def test_approval_tool_pauses_workflow():
    from flux import ExecutionContext, workflow
    from flux.domain.events import ExecutionEventType

    @workflow
    async def test_wf(ctx: ExecutionContext):
        results = await execute_tools(
            [{"id": "c1", "name": "dangerous_tool", "arguments": {"target": "prod"}}],
            [dangerous_tool_approved],
        )
        return results

    ctx = test_wf.run()
    assert ctx.is_paused

    pause_events = [e for e in ctx.events if e.type == ExecutionEventType.WORKFLOW_PAUSED]
    assert len(pause_events) == 1
    pause_output = pause_events[0].value["output"]
    assert pause_output["type"] == "tool_approval"
    assert pause_output["tool"] == "dangerous_tool"
    assert pause_output["arguments"] == {"target": "prod"}


def test_approval_tool_executes_after_approve():
    from flux import ExecutionContext, workflow

    @workflow
    async def test_wf(ctx: ExecutionContext):
        results = await execute_tools(
            [{"id": "c1", "name": "dangerous_tool", "arguments": {"target": "prod"}}],
            [dangerous_tool_approved],
        )
        return results

    ctx = test_wf.run()
    assert ctx.is_paused

    ctx = test_wf.resume(ctx.execution_id, {"approved": True})
    assert ctx.has_succeeded
    assert ctx.output[0]["output"] == "deleted:prod"


def test_approval_tool_rejected_returns_error():
    from flux import ExecutionContext, workflow

    @workflow
    async def test_wf(ctx: ExecutionContext):
        results = await execute_tools(
            [{"id": "c1", "name": "dangerous_tool", "arguments": {"target": "prod"}}],
            [dangerous_tool_approved],
        )
        return results

    ctx = test_wf.run()
    assert ctx.is_paused

    ctx = test_wf.resume(ctx.execution_id, {"approved": False})
    assert ctx.has_succeeded
    assert "rejected" in ctx.output[0]["output"].lower()


def test_approval_always_approve_skips_subsequent():
    from flux import ExecutionContext, workflow

    @workflow
    async def test_wf(ctx: ExecutionContext):
        approved_set: set[str] = set()
        r1 = await execute_tools(
            [{"id": "c1", "name": "dangerous_tool", "arguments": {"target": "first"}}],
            [dangerous_tool_approved],
            always_approved=approved_set,
        )
        r2 = await execute_tools(
            [{"id": "c2", "name": "dangerous_tool", "arguments": {"target": "second"}}],
            [dangerous_tool_approved],
            always_approved=approved_set,
        )
        return {"r1": r1, "r2": r2}

    ctx = test_wf.run()
    assert ctx.is_paused

    ctx = test_wf.resume(ctx.execution_id, {"approved": True, "always_approve": True})
    assert ctx.has_succeeded
    assert ctx.output["r1"][0]["output"] == "deleted:first"
    assert ctx.output["r2"][0]["output"] == "deleted:second"


def test_approval_autonomous_mode_skips_all():
    from flux import ExecutionContext, workflow

    @workflow
    async def test_wf(ctx: ExecutionContext):
        results = await execute_tools(
            [{"id": "c1", "name": "dangerous_tool", "arguments": {"target": "prod"}}],
            [dangerous_tool_approved],
            approval_mode="autonomous",
        )
        return results

    ctx = test_wf.run()
    assert ctx.has_succeeded
    assert ctx.output[0]["output"] == "deleted:prod"


def test_non_approval_tools_unaffected():
    from flux import ExecutionContext, workflow

    @workflow
    async def test_wf(ctx: ExecutionContext):
        results = await execute_tools(
            [{"id": "c1", "name": "search_web", "arguments": {"query": "hello"}}],
            [search_web],
        )
        return results

    ctx = test_wf.run()
    assert ctx.has_succeeded
    assert ctx.output[0]["output"] == "Results for: hello"


def test_build_tools_preamble_includes_approval_section():
    preamble = build_tools_preamble([search_web, dangerous_tool_approved])
    assert "Tool Approval" in preamble
    assert "dangerous_tool" in preamble
    assert "search_web" not in preamble.split("Tool Approval")[1]


def test_build_tools_preamble_no_approval_section_without_approval_tools():
    preamble = build_tools_preamble([search_web])
    assert "Tool Approval" not in preamble
