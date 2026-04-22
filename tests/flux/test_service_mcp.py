from __future__ import annotations

import pytest

from flux.service_mcp import EndpointInfo, ServiceMCPServer


class FakeProvider:
    def __init__(self, endpoints: dict[str, EndpointInfo]):
        self._endpoints = endpoints

    async def get_endpoints(self) -> dict[str, EndpointInfo]:
        return self._endpoints


def _make_endpoint(
    name: str,
    namespace: str = "default",
    version: int = 1,
    input_schema: dict | None = None,
    description: str | None = None,
) -> EndpointInfo:
    return EndpointInfo(
        name=name,
        namespace=namespace,
        version=version,
        input_schema=input_schema,
        description=description,
    )


def _build_server(endpoints: dict[str, EndpointInfo]) -> ServiceMCPServer:
    provider = FakeProvider(endpoints)
    server = ServiceMCPServer("test-service", provider)
    server._generate_tools(endpoints)
    return server


def _tool_names(server: ServiceMCPServer) -> set[str]:
    return server.tool_names


class TestRefresh:
    async def test_refresh_adds_new_workflows(self):
        ep1 = _make_endpoint("greet")
        provider = FakeProvider({"greet": ep1})
        server = ServiceMCPServer("test-service", provider)
        server._generate_tools({"greet": ep1})
        assert "greet" in server.tool_names
        assert "farewell" not in server.tool_names

        ep2 = _make_endpoint("farewell")
        provider._endpoints = {"greet": ep1, "farewell": ep2}
        await server.refresh()
        assert "farewell" in server.tool_names
        assert "greet" in server.tool_names

    async def test_refresh_warns_on_removed_workflows(self, caplog):
        import logging

        ep1 = _make_endpoint("greet")
        ep2 = _make_endpoint("farewell")
        provider = FakeProvider({"greet": ep1, "farewell": ep2})
        server = ServiceMCPServer("test-service", provider)
        server._generate_tools({"greet": ep1, "farewell": ep2})

        provider._endpoints = {"greet": ep1}
        with caplog.at_level(logging.WARNING):
            await server.refresh()
        assert any("farewell" in msg for msg in caplog.messages)

    async def test_refresh_noop_when_unchanged(self):
        ep1 = _make_endpoint("greet")
        provider = FakeProvider({"greet": ep1})
        server = ServiceMCPServer("test-service", provider)
        server._generate_tools({"greet": ep1})
        tool_count_before = len(server.tool_names)
        await server.refresh()
        assert len(server.tool_names) == tool_count_before


class TestToolGeneration:
    def test_generates_five_tools_per_workflow(self):
        ep = _make_endpoint("greet")
        server = _build_server({"greet": ep})
        names = _tool_names(server)
        assert names == {
            "greet",
            "greet_async",
            "resume_greet",
            "resume_greet_async",
            "status_greet",
        }

    def test_multiple_workflows(self):
        endpoints = {
            "greet": _make_endpoint("greet"),
            "farewell": _make_endpoint("farewell"),
        }
        server = _build_server(endpoints)
        names = _tool_names(server)
        assert len(names) == 10
        assert "greet" in names
        assert "farewell_async" in names
        assert "resume_farewell" in names
        assert "status_greet" in names

    @pytest.mark.asyncio
    async def test_pydantic_schema_generates_typed_params(self):
        schema = {
            "properties": {
                "name": {"type": "string"},
                "age": {"type": "integer"},
            },
            "required": ["name"],
        }
        ep = _make_endpoint("register", input_schema=schema)
        server = _build_server({"register": ep})

        fn = await server.get_tool_function("register")
        assert fn is not None
        assert fn.__annotations__["name"] is str
        assert fn.__annotations__["age"] is int

    @pytest.mark.asyncio
    async def test_no_schema_uses_input_str(self):
        ep = _make_endpoint("simple")
        server = _build_server({"simple": ep})

        fn = await server.get_tool_function("simple")
        assert fn is not None
        assert "input" in fn.__annotations__

    @pytest.mark.asyncio
    async def test_complex_types_in_schema(self):
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "tags": {"type": "array"},
                "metadata": {"type": "object"},
                "count": {"type": "integer"},
                "active": {"type": "boolean"},
            },
            "required": ["name", "tags"],
        }
        ep = _make_endpoint("complex", input_schema=schema)
        server = _build_server({"complex": ep})
        assert "complex" in server.tool_names

        fn = await server.get_tool_function("complex")
        assert fn is not None
        annotations = fn.__annotations__
        assert annotations.get("name") is str
        assert annotations.get("tags") is list
        assert annotations.get("metadata") is dict
        assert annotations.get("count") is int
        assert annotations.get("active") is bool

    @pytest.mark.asyncio
    async def test_tool_description_from_metadata(self):
        ep = _make_endpoint("deploy", description="Deploy to production")
        server = _build_server({"deploy": ep})

        fn = await server.get_tool_function("deploy")
        assert fn is not None
        assert "Deploy to production" in (fn.__doc__ or "")
