from __future__ import annotations


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
    return {t.name for t in server.mcp._tool_manager.list_tools()}


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

    def test_pydantic_schema_generates_typed_params(self):
        schema = {
            "properties": {
                "name": {"type": "string"},
                "age": {"type": "integer"},
            },
            "required": ["name"],
        }
        ep = _make_endpoint("register", input_schema=schema)
        server = _build_server({"register": ep})

        tool = None
        for t in server.mcp._tool_manager.list_tools():
            if t.name == "register":
                tool = t
                break
        assert tool is not None
        fn = tool.fn
        assert fn.__annotations__["name"] is str
        assert fn.__annotations__["age"] is int

    def test_no_schema_uses_input_str(self):
        ep = _make_endpoint("simple")
        server = _build_server({"simple": ep})

        tool = None
        for t in server.mcp._tool_manager.list_tools():
            if t.name == "simple":
                tool = t
                break
        assert tool is not None
        fn = tool.fn
        assert "input" in fn.__annotations__

    def test_tool_description_from_metadata(self):
        ep = _make_endpoint("deploy", description="Deploy to production")
        server = _build_server({"deploy": ep})

        tool = None
        for t in server.mcp._tool_manager.list_tools():
            if t.name == "deploy":
                tool = t
                break
        assert tool is not None
        assert "Deploy to production" in (tool.fn.__doc__ or "")
