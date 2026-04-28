from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from flux.service_mcp import EndpointInfo, ServiceMCPServer, create_service_mcp_server
from flux.service_proxy import MCPRouteMiddleware


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

        fn = server.get_tool_function("register")
        assert fn is not None
        assert fn.__annotations__["name"] is str
        assert fn.__annotations__["age"] is int

    def test_no_schema_uses_input_str(self):
        ep = _make_endpoint("simple")
        server = _build_server({"simple": ep})

        fn = server.get_tool_function("simple")
        assert fn is not None
        assert "input" in fn.__annotations__

    def test_complex_types_in_schema(self):
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

        fn = server.get_tool_function("complex")
        assert fn is not None
        annotations = fn.__annotations__
        assert annotations.get("name") is str
        assert annotations.get("tags") is list
        assert annotations.get("metadata") is dict
        assert annotations.get("count") is int
        assert annotations.get("active") is bool

    def test_tool_description_from_metadata(self):
        ep = _make_endpoint("deploy", description="Deploy to production")
        server = _build_server({"deploy": ep})

        fn = server.get_tool_function("deploy")
        assert fn is not None
        assert "Deploy to production" in (fn.__doc__ or "")


class TestAuthProvider:
    def test_no_auth_by_default(self):
        with patch("flux.service_mcp.FastMCP") as mock_cls:
            ep = _make_endpoint("greet")
            provider = FakeProvider({"greet": ep})
            ServiceMCPServer("test-service", provider)
            mock_cls.assert_called_once_with("flux-service-test-service", auth=None)

    def test_auth_provider_passed_to_fastmcp(self):
        fake_auth = MagicMock()
        with patch("flux.service_mcp.FastMCP") as mock_cls:
            provider = FakeProvider({"greet": _make_endpoint("greet")})
            ServiceMCPServer("test-service", provider, auth=fake_auth)
            mock_cls.assert_called_once_with("flux-service-test-service", auth=fake_auth)

    def test_create_service_mcp_server_with_auth(self):
        fake_auth = MagicMock()
        fake_client = MagicMock()
        with patch("flux.service_mcp.FastMCP") as mock_cls:
            create_service_mcp_server("svc", fake_client, auth=fake_auth)
            mock_cls.assert_called_once_with("flux-service-svc", auth=fake_auth)

    def test_create_service_mcp_server_without_auth(self):
        fake_client = MagicMock()
        with patch("flux.service_mcp.FastMCP") as mock_cls:
            create_service_mcp_server("svc", fake_client)
            mock_cls.assert_called_once_with("flux-service-svc", auth=None)


class TestMCPRouteMiddleware:
    def test_mcp_path_is_mcp_route(self):
        assert MCPRouteMiddleware._is_mcp_route("/mcp") is True
        assert MCPRouteMiddleware._is_mcp_route("/mcp/") is True
        assert MCPRouteMiddleware._is_mcp_route("/mcp/something") is True

    def test_well_known_is_mcp_route(self):
        assert MCPRouteMiddleware._is_mcp_route("/.well-known/oauth-protected-resource") is True
        assert MCPRouteMiddleware._is_mcp_route("/.well-known/oauth-authorization-server") is True

    def test_health_is_not_mcp_route(self):
        assert MCPRouteMiddleware._is_mcp_route("/health") is False

    def test_workflow_is_not_mcp_route(self):
        assert MCPRouteMiddleware._is_mcp_route("/invoice") is False
        assert MCPRouteMiddleware._is_mcp_route("/invoice/status/abc") is False

    def test_mcp_prefixed_workflow_is_not_mcp_route(self):
        assert MCPRouteMiddleware._is_mcp_route("/mcp_billing") is False
        assert MCPRouteMiddleware._is_mcp_route("/mcptest") is False
        assert MCPRouteMiddleware._is_mcp_route("/mcp-workflow") is False

    @pytest.mark.asyncio
    async def test_http_mcp_routed_to_mcp_app(self):
        calls = {"main": [], "mcp": []}

        async def main_app(scope, receive, send):
            calls["main"].append(scope["path"])

        async def mcp_app(scope, receive, send):
            calls["mcp"].append(scope["path"])

        middleware = MCPRouteMiddleware(main_app, mcp_app=mcp_app)
        await middleware({"type": "http", "path": "/mcp"}, None, None)
        assert calls["mcp"] == ["/mcp"]
        assert calls["main"] == []

    @pytest.mark.asyncio
    async def test_http_well_known_routed_to_mcp_app(self):
        calls = {"main": [], "mcp": []}

        async def main_app(scope, receive, send):
            calls["main"].append(scope["path"])

        async def mcp_app(scope, receive, send):
            calls["mcp"].append(scope["path"])

        middleware = MCPRouteMiddleware(main_app, mcp_app=mcp_app)
        await middleware(
            {"type": "http", "path": "/.well-known/oauth-protected-resource"},
            None,
            None,
        )
        assert calls["mcp"] == ["/.well-known/oauth-protected-resource"]
        assert calls["main"] == []

    @pytest.mark.asyncio
    async def test_http_workflow_routed_to_main_app(self):
        calls = {"main": [], "mcp": []}

        async def main_app(scope, receive, send):
            calls["main"].append(scope["path"])

        async def mcp_app(scope, receive, send):
            calls["mcp"].append(scope["path"])

        middleware = MCPRouteMiddleware(main_app, mcp_app=mcp_app)
        await middleware({"type": "http", "path": "/invoice"}, None, None)
        assert calls["main"] == ["/invoice"]
        assert calls["mcp"] == []

    @pytest.mark.asyncio
    async def test_non_http_scope_passed_to_main_app(self):
        calls = {"main": [], "mcp": []}

        async def main_app(scope, receive, send):
            calls["main"].append(scope["type"])

        async def mcp_app(scope, receive, send):
            calls["mcp"].append(scope["type"])

        middleware = MCPRouteMiddleware(main_app, mcp_app=mcp_app)
        await middleware({"type": "websocket", "path": "/mcp"}, None, None)
        assert calls["main"] == ["websocket"]
        assert calls["mcp"] == []
