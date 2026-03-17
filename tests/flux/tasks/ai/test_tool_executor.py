from __future__ import annotations

import pytest

from flux import task
from flux.tasks.ai.tool_executor import build_tool_schemas, execute_tools


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
