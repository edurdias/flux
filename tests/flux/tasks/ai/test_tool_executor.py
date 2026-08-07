from __future__ import annotations

import asyncio
import time

import pytest

from flux import task
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


@task.with_options(requires_approval=True)
async def dangerous_tool(target: str) -> str:
    """Delete something dangerous."""
    return f"deleted:{target}"


# Alias kept for readability of existing test names; the task itself is gated.
dangerous_tool_approved = dangerous_tool


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


def test_pause_in_a_turn_drains_the_other_tools():
    """An approval-gated tool suspends the turn; its siblings must not outlive it.

    Durability over promptness: a sibling that can finish inside the drain
    window does, so its terminal event is recorded and replay short-circuits it
    on resume. What must never happen is a sibling still running after the
    pause propagates -- it would checkpoint against an execution already
    recorded PAUSED.
    """
    from flux import ExecutionContext, workflow
    from flux.errors import PauseRequested

    finished = {"sibling": False}

    @task
    async def sibling_tool() -> str:
        await asyncio.sleep(0.2)
        finished["sibling"] = True
        return "too late"

    @task
    async def gated_tool() -> str:
        raise PauseRequested(name="gate")

    @workflow
    async def test_wf(ctx: ExecutionContext):
        calls = [
            {"id": "1", "name": "sibling_tool", "arguments": {}},
            {"id": "2", "name": "gated_tool", "arguments": {}},
        ]
        try:
            await execute_tools(calls, [sibling_tool, gated_tool])
        except PauseRequested:
            # Already terminated: gather_batch does not re-raise until every
            # member of the batch has finished or been cancelled.
            return finished["sibling"]
        return None

    ctx = test_wf.run()
    assert ctx.has_succeeded, ctx.output
    assert ctx.output is True, "sibling tool was killed instead of drained"


def test_risk_option_validates_and_inherits():
    with pytest.raises(ValueError, match="risk must be one of"):

        @task.with_options(risk="destructive")
        async def bad_tool() -> str:
            return "x"

    @task.with_options(risk="write")
    async def write_tool() -> str:
        return "x"

    assert write_tool.risk == "write"
    # with_options-derived variants (the executor's per-iteration rename)
    # keep the declaration.
    assert write_tool.with_options(name="write_tool_1").risk == "write"


def test_with_options_preserves_approval_target():
    # The executor renames tools per iteration via with_options; the #143
    # target declaration must survive that (it used to be dropped).
    @task.with_options(requires_approval=True, approval_target="path")
    async def gated(path: str) -> str:
        return path

    assert gated.with_options(name="gated_1").approval_target == "path"


def test_write_then_read_runs_in_emission_order():
    """The #140 acceptance case: write_file then a read of the same resource
    must be deterministic — the read always observes the completed write."""
    from flux import ExecutionContext, workflow

    log: list[str] = []

    @task.with_options(risk="write")
    async def write_tool(value: str) -> str:
        # Long enough that a concurrent read would win the race.
        await asyncio.sleep(0.15)
        log.append(f"write:{value}")
        return f"wrote:{value}"

    @task.with_options(risk="read")
    async def read_tool() -> str:
        log.append("read")
        return ",".join(log)

    @workflow
    async def test_wf(ctx: ExecutionContext):
        calls = [
            {"id": "1", "name": "write_tool", "arguments": {"value": "x"}},
            {"id": "2", "name": "read_tool", "arguments": {}},
        ]
        return await execute_tools(calls, [write_tool, read_tool])

    ctx = test_wf.run()
    assert ctx.has_succeeded
    assert ctx.output[0]["output"] == "wrote:x"
    # The read ran after the write settled and saw its effect.
    assert ctx.output[1]["output"] == "write:x,read"


def test_reads_between_barriers_stay_ordered_relative_to_them():
    from flux import ExecutionContext, workflow

    log: list[str] = []

    @task.with_options(risk="read")
    async def probe(label: str) -> str:
        log.append(f"read:{label}")
        return label

    @task.with_options(risk="exec")
    async def run_cmd() -> str:
        await asyncio.sleep(0.1)
        log.append("exec")
        return "ran"

    @workflow
    async def test_wf(ctx: ExecutionContext):
        calls = [
            {"id": "1", "name": "probe", "arguments": {"label": "before"}},
            {"id": "2", "name": "run_cmd", "arguments": {}},
            {"id": "3", "name": "probe", "arguments": {"label": "after"}},
        ]
        results = await execute_tools(calls, [probe, run_cmd])
        return {"results": results, "log": log}

    ctx = test_wf.run()
    assert ctx.has_succeeded
    assert ctx.output["log"] == ["read:before", "exec", "read:after"]
    # Results stay in emission order.
    assert [r["output"] for r in ctx.output["results"]] == ["before", "ran", "after"]


def test_declared_reads_still_run_concurrently():
    from flux import ExecutionContext, workflow

    @task.with_options(risk="read")
    async def slow_read(label: str) -> str:
        await asyncio.sleep(0.3)
        return f"done:{label}"

    @workflow
    async def test_wf(ctx: ExecutionContext):
        calls = [
            {"id": str(i), "name": "slow_read", "arguments": {"label": str(i)}} for i in range(3)
        ]
        start = time.monotonic()
        results = await execute_tools(calls, [slow_read])
        return {"n": len(results), "elapsed": time.monotonic() - start}

    ctx = test_wf.run()
    assert ctx.has_succeeded
    assert ctx.output["n"] == 3
    # Concurrent: well under the 0.9s a serialized run would take.
    assert ctx.output["elapsed"] < 0.8


def test_undeclared_approval_gated_tool_is_a_barrier():
    """Zero-config fallback: requires_approval (declared) means "not a read"."""
    from flux import ExecutionContext, workflow

    log: list[str] = []

    @task.with_options(requires_approval=True)
    async def consequential(value: str) -> str:
        await asyncio.sleep(0.15)
        log.append(f"effect:{value}")
        return f"did:{value}"

    @task
    async def observe() -> str:
        log.append("observe")
        return ",".join(log)

    @workflow
    async def test_wf(ctx: ExecutionContext):
        calls = [
            {"id": "1", "name": "consequential", "arguments": {"value": "x"}},
            {"id": "2", "name": "observe", "arguments": {}},
        ]
        # autonomous lifts the gate at execution time; the *declared*
        # requires_approval still classifies the tool as a barrier.
        return await execute_tools(calls, [consequential, observe], autonomy="autonomous")

    ctx = test_wf.run()
    assert ctx.has_succeeded
    assert ctx.output[1]["output"] == "effect:x,observe"


def test_parallel_tool_calls_false_serializes_everything():
    from flux import ExecutionContext, workflow

    log: list[str] = []

    @task
    async def plain(label: str) -> str:
        await asyncio.sleep(0.1)
        log.append(label)
        return label

    @workflow
    async def test_wf(ctx: ExecutionContext):
        calls = [
            {"id": "1", "name": "plain", "arguments": {"label": "a"}},
            {"id": "2", "name": "plain", "arguments": {"label": "b"}},
            {"id": "3", "name": "plain", "arguments": {"label": "c"}},
        ]
        start = time.monotonic()
        await execute_tools(calls, [plain], parallel_tool_calls=False)
        return {"log": log, "elapsed": time.monotonic() - start}

    ctx = test_wf.run()
    assert ctx.has_succeeded
    assert ctx.output["log"] == ["a", "b", "c"]
    assert ctx.output["elapsed"] >= 0.25  # three strictly serial 0.1s calls


def test_parallel_tool_calls_false_serializes_around_unknown_tools():
    """An unknown tool in the turn must not re-open concurrency when the
    model cannot do parallel calls — every call is a barrier, known or not."""
    from flux import ExecutionContext, workflow

    log: list[str] = []

    @task
    async def plain(label: str) -> str:
        await asyncio.sleep(0.1)
        log.append(label)
        return label

    @workflow
    async def test_wf(ctx: ExecutionContext):
        calls = [
            {"id": "1", "name": "plain", "arguments": {"label": "a"}},
            {"id": "2", "name": "not_a_tool", "arguments": {}},
            {"id": "3", "name": "plain", "arguments": {"label": "b"}},
        ]
        results = await execute_tools(calls, [plain], parallel_tool_calls=False)
        return {"log": log, "results": results}

    ctx = test_wf.run()
    assert ctx.has_succeeded
    assert ctx.output["log"] == ["a", "b"]
    assert "Unknown tool" in ctx.output["results"][1]["output"]
    assert [r["output"] for r in (ctx.output["results"][0], ctx.output["results"][2])] == ["a", "b"]


def test_pause_in_a_barrier_settles_prior_reads_and_stops_later_calls():
    from flux import ExecutionContext, workflow
    from flux.errors import PauseRequested

    log: list[str] = []

    @task.with_options(risk="read")
    async def early_read() -> str:
        log.append("early")
        return "early"

    @task.with_options(risk="write")
    async def pausing_write() -> str:
        raise PauseRequested(name="gate")

    @task.with_options(risk="read")
    async def late_read() -> str:
        log.append("late")
        return "late"

    @workflow
    async def test_wf(ctx: ExecutionContext):
        calls = [
            {"id": "1", "name": "early_read", "arguments": {}},
            {"id": "2", "name": "pausing_write", "arguments": {}},
            {"id": "3", "name": "late_read", "arguments": {}},
        ]
        try:
            await execute_tools(calls, [early_read, pausing_write, late_read])
        except PauseRequested:
            return log
        return None

    ctx = test_wf.run()
    assert ctx.has_succeeded, ctx.output
    # The read before the barrier completed; the one after it never started.
    assert ctx.output == ["early"]


def test_pause_in_a_turn_drains_the_other_tools_when_bounded():
    """Same contract on the max_concurrent path."""
    from flux import ExecutionContext, workflow
    from flux.errors import PauseRequested

    finished = {"sibling": False}

    @task
    async def sibling_tool() -> str:
        await asyncio.sleep(0.2)
        finished["sibling"] = True
        return "too late"

    @task
    async def gated_tool() -> str:
        raise PauseRequested(name="gate")

    @workflow
    async def test_wf(ctx: ExecutionContext):
        calls = [
            {"id": "1", "name": "sibling_tool", "arguments": {}},
            {"id": "2", "name": "gated_tool", "arguments": {}},
        ]
        try:
            await execute_tools(calls, [sibling_tool, gated_tool], max_concurrent=4)
        except PauseRequested:
            return finished["sibling"]
        return None

    ctx = test_wf.run()
    assert ctx.has_succeeded, ctx.output
    assert ctx.output is True, "sibling tool was killed instead of drained"
