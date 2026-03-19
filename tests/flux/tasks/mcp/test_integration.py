from __future__ import annotations

from fastmcp import FastMCP

from flux import ExecutionContext, workflow
from flux.tasks.mcp import mcp


def _create_test_server() -> FastMCP:
    server = FastMCP("test-server")

    @server.tool()
    def get_weather(city: str) -> str:
        """Get weather for a city."""
        return f"Sunny in {city}"

    @server.tool()
    def add(a: int, b: int) -> str:
        """Add two numbers."""
        return str(a + b)

    return server


def test_discover_and_call_tool():
    server = _create_test_server()

    @workflow
    async def test_wf(ctx: ExecutionContext):
        async with mcp(server, name="test") as client:
            tools = await client.discover()
            result = await tools.get_weather(city="London")
            return result

    ctx = test_wf.run()
    assert ctx.has_succeeded
    assert "London" in str(ctx.output)


def test_discover_returns_correct_tool_count():
    server = _create_test_server()

    @workflow
    async def test_wf(ctx: ExecutionContext):
        async with mcp(server, name="test") as client:
            tools = await client.discover()
            return len(tools)

    ctx = test_wf.run()
    assert ctx.has_succeeded
    assert ctx.output == 2


def test_multiple_tool_calls():
    server = _create_test_server()

    @workflow
    async def test_wf(ctx: ExecutionContext):
        async with mcp(server, name="test") as client:
            tools = await client.discover()
            weather = await tools.get_weather(city="Paris")
            total = await tools.add(a=3, b=4)
            return {"weather": weather, "total": total}

    ctx = test_wf.run()
    assert ctx.has_succeeded
    assert "Paris" in str(ctx.output["weather"])
    assert "7" in str(ctx.output["total"])


def test_tools_iterable():
    server = _create_test_server()

    @workflow
    async def test_wf(ctx: ExecutionContext):
        async with mcp(server, name="test") as client:
            tools = await client.discover()
            return [t.name for t in tools]

    ctx = test_wf.run()
    assert ctx.has_succeeded
    assert len(ctx.output) == 2
    assert all("mcp_test_" in name for name in ctx.output)


def test_event_log_contains_mcp_tasks():
    server = _create_test_server()

    @workflow
    async def test_wf(ctx: ExecutionContext):
        async with mcp(server, name="test") as client:
            tools = await client.discover()
            await tools.get_weather(city="London")
            return "done"

    ctx = test_wf.run()
    assert ctx.has_succeeded
    task_names = [e.name for e in ctx.events if "mcp_test_" in (e.name or "")]
    assert any("mcp_test_discover" in n for n in task_names)
    assert any("mcp_test_get_weather" in n for n in task_names)
