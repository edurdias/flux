import inspect
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

import httpx
from fastmcp import FastMCP

from flux.utils import get_logger

if TYPE_CHECKING:
    from fastmcp.server.auth import AuthProvider

logger = get_logger(__name__)

JSON_TYPE_MAP: dict[str, type] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "array": list,
    "object": dict,
    "null": type(None),
}


@dataclass
class EndpointInfo:
    name: str
    namespace: str
    version: int
    input_schema: dict | None = None
    description: str | None = None


@runtime_checkable
class EndpointProvider(Protocol):
    async def get_endpoints(self) -> dict[str, EndpointInfo]:
        ...


class ServiceMCPServer:
    def __init__(
        self,
        service_name: str,
        provider: EndpointProvider,
        auth: "AuthProvider | None" = None,
    ):
        self.service_name = service_name
        self.provider = provider
        self.mcp = FastMCP(f"flux-service-{service_name}", auth=auth)
        self._registered_tools: set[str] = set()
        self._tool_names: set[str] = set()

    @property
    def tool_names(self) -> set[str]:
        return set(self._tool_names)

    async def get_tool_function(self, name: str):
        tool = await self.mcp._tool_manager.get_tool(name)
        return tool.fn if tool else None

    async def refresh(self) -> None:
        endpoints = await self.provider.get_endpoints()
        current_names = set(endpoints.keys())
        registered_names = set(self._registered_tools)
        if current_names != registered_names:
            new_endpoints = {k: v for k, v in endpoints.items() if k not in registered_names}
            if new_endpoints:
                logger.info(
                    "MCP refresh: adding tools for new workflows: %s",
                    list(new_endpoints.keys()),
                )
                self._generate_tools(new_endpoints)
            removed = registered_names - current_names
            if removed:
                logger.warning(
                    "MCP refresh: workflows removed but tools cannot be unregistered: %s. "
                    "Restart the service to clean up stale tools.",
                    list(removed),
                )

    def _generate_tools(self, endpoints: dict[str, EndpointInfo]) -> None:
        # Tools are generated per-session. Existing tool names are skipped —
        # schema/description changes are picked up on the next client connection.
        for key, info in endpoints.items():
            if key in self._registered_tools:
                continue
            self._register_run_tool(info, mode="sync")
            self._register_run_tool(info, mode="async")
            self._register_resume_tool(info, mode="sync")
            self._register_resume_tool(info, mode="async")
            self._register_status_tool(info)
            self._registered_tools.add(key)
            suffix_async = "_async"
            self._tool_names.update(
                {
                    info.name,
                    f"{info.name}{suffix_async}",
                    f"resume_{info.name}",
                    f"resume_{info.name}{suffix_async}",
                    f"status_{info.name}",
                },
            )

    def _register_run_tool(self, info: EndpointInfo, mode: str) -> None:
        suffix = "_async" if mode == "async" else ""
        tool_name = f"{info.name}{suffix}"
        namespace = info.namespace
        name = info.name
        desc = self._build_run_description(info, mode)

        if info.input_schema and info.input_schema.get("properties"):
            self._register_typed_run(tool_name, namespace, name, mode, info, desc)
        else:
            self._register_generic_run(tool_name, namespace, name, mode, desc)

    def _build_run_description(self, info: EndpointInfo, mode: str) -> str:
        base = info.description or f"Run the {info.name} workflow"
        if mode == "async":
            base += " (async — returns execution ID immediately)"
        return base

    def _register_typed_run(
        self,
        tool_name: str,
        namespace: str,
        name: str,
        mode: str,
        info: EndpointInfo,
        description: str,
    ) -> None:
        assert info.input_schema is not None
        properties = info.input_schema["properties"]
        required = set(info.input_schema.get("required", []))
        param_lines = [f"  {p}: {properties[p].get('type', 'string')}" for p in properties]
        docstring = f"{description}\n\nParameters:\n" + "\n".join(param_lines)
        server = self

        params = []
        annotations: dict[str, Any] = {"return": dict[str, Any]}
        for prop_name, prop_schema in properties.items():
            py_type = JSON_TYPE_MAP.get(prop_schema.get("type", "string"), str)
            annotations[prop_name] = py_type
            if prop_name in required:
                params.append(
                    inspect.Parameter(
                        prop_name,
                        inspect.Parameter.KEYWORD_ONLY,
                        annotation=py_type,
                    ),
                )
            else:
                default = prop_schema.get("default")
                params.append(
                    inspect.Parameter(
                        prop_name,
                        inspect.Parameter.KEYWORD_ONLY,
                        default=default,
                        annotation=py_type,
                    ),
                )

        sig = inspect.Signature(params, return_annotation=dict[str, Any])

        async def typed_handler(**kwargs: Any) -> dict[str, Any]:
            return await server._execute_run(namespace, name, mode, kwargs)

        typed_handler.__signature__ = sig  # type: ignore[attr-defined]
        typed_handler.__annotations__ = annotations
        typed_handler.__name__ = tool_name
        typed_handler.__qualname__ = tool_name
        typed_handler.__doc__ = docstring

        self.mcp.tool(name=tool_name)(typed_handler)

    def _register_generic_run(
        self,
        tool_name: str,
        namespace: str,
        name: str,
        mode: str,
        description: str,
    ) -> None:
        server = self

        async def generic_handler(input: str = "null") -> dict[str, Any]:
            input_data = _parse_json_input(input)
            return await server._execute_run(namespace, name, mode, input_data)

        generic_handler.__name__ = tool_name
        generic_handler.__qualname__ = tool_name
        generic_handler.__doc__ = description

        self.mcp.tool(name=tool_name)(generic_handler)

    def _register_resume_tool(self, info: EndpointInfo, mode: str) -> None:
        suffix = "_async" if mode == "async" else ""
        tool_name = f"resume_{info.name}{suffix}"
        namespace = info.namespace
        name = info.name
        desc = f"Resume a paused {info.name} execution"
        if mode == "async":
            desc += " (async)"
        server = self

        async def resume_handler(execution_id: str, input: str = "null") -> dict[str, Any]:
            input_data = _parse_json_input(input)
            return await server._execute_resume(namespace, name, execution_id, mode, input_data)

        resume_handler.__name__ = tool_name
        resume_handler.__qualname__ = tool_name
        resume_handler.__doc__ = desc

        self.mcp.tool(name=tool_name)(resume_handler)

    def _register_status_tool(self, info: EndpointInfo) -> None:
        tool_name = f"status_{info.name}"
        namespace = info.namespace
        name = info.name
        desc = f"Check the status of a {info.name} execution"
        server = self

        async def status_handler(execution_id: str) -> dict[str, Any]:
            return await server._execute_status(namespace, name, execution_id)

        status_handler.__name__ = tool_name
        status_handler.__qualname__ = tool_name
        status_handler.__doc__ = desc

        self.mcp.tool(name=tool_name)(status_handler)

    async def _execute_run(
        self,
        namespace: str,
        name: str,
        mode: str,
        input_data: Any,
    ) -> dict[str, Any]:
        raise NotImplementedError

    async def _execute_resume(
        self,
        namespace: str,
        name: str,
        execution_id: str,
        mode: str,
        input_data: Any,
    ) -> dict[str, Any]:
        raise NotImplementedError

    async def _execute_status(self, namespace: str, name: str, execution_id: str) -> dict[str, Any]:
        raise NotImplementedError


class ProxyBackedMCPServer(ServiceMCPServer):
    def __init__(
        self,
        service_name: str,
        provider: EndpointProvider,
        client: httpx.AsyncClient,
        auth: "AuthProvider | None" = None,
    ):
        super().__init__(service_name, provider, auth=auth)
        self._client = client

    async def _execute_run(
        self,
        namespace: str,
        name: str,
        mode: str,
        input_data: Any,
    ) -> dict[str, Any]:
        try:
            response = await self._client.post(
                f"/workflows/{namespace}/{name}/run/{mode}",
                json=input_data,
                timeout=300.0 if mode == "sync" else 30.0,
            )
            response.raise_for_status()
            result = response.json()
            return self._format_run_result(name, result, mode)
        except httpx.HTTPStatusError as e:
            return {"success": False, "error": f"HTTP {e.response.status_code}: {e.response.text}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def _execute_resume(
        self,
        namespace: str,
        name: str,
        execution_id: str,
        mode: str,
        input_data: Any,
    ) -> dict[str, Any]:
        try:
            response = await self._client.post(
                f"/workflows/{namespace}/{name}/resume/{execution_id}/{mode}",
                json=input_data,
                timeout=300.0 if mode == "sync" else 30.0,
            )
            response.raise_for_status()
            result = response.json()
            return self._format_run_result(name, result, mode)
        except httpx.HTTPStatusError as e:
            return {"success": False, "error": f"HTTP {e.response.status_code}: {e.response.text}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def _execute_status(self, namespace: str, name: str, execution_id: str) -> dict[str, Any]:
        try:
            response = await self._client.get(
                f"/workflows/{namespace}/{name}/status/{execution_id}",
            )
            response.raise_for_status()
            result = response.json()
            state = result.get("state", "UNKNOWN")
            return {
                "success": True,
                "execution_id": execution_id,
                "state": state,
                "output": result.get("output"),
            }
        except httpx.HTTPStatusError as e:
            return {"success": False, "error": f"HTTP {e.response.status_code}: {e.response.text}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @staticmethod
    def _format_run_result(name: str, result: dict, mode: str) -> dict[str, Any]:
        execution_id = result.get("execution_id")
        state = result.get("state", "UNKNOWN")
        output = result.get("output")
        resp: dict[str, Any] = {
            "success": True,
            "execution_id": execution_id,
            "state": state,
            "output": output,
        }
        if state == "PAUSED":
            resp[
                "message"
            ] = f"Use resume_{name}(execution_id='{execution_id}', input='...') to continue."
        if mode == "async":
            resp["message"] = f"Use status_{name}(execution_id='{execution_id}') to check progress."
        return resp


@dataclass
class ProxyEndpointProvider:
    service_name: str
    _client: httpx.AsyncClient

    def __init__(self, service_name: str, client: httpx.AsyncClient):
        self.service_name = service_name
        self._client = client

    async def get_endpoints(self) -> dict[str, EndpointInfo]:
        response = await self._client.get(f"/services/{self.service_name}")
        response.raise_for_status()
        data = response.json()
        endpoints: dict[str, EndpointInfo] = {}
        for ep in data.get("endpoints", []):
            info = EndpointInfo(
                name=ep["name"],
                namespace=ep.get("namespace", "default"),
                version=ep.get("version", 1),
                input_schema=ep.get("input_schema"),
                description=ep.get("description"),
            )
            endpoints[info.name] = info
        return endpoints


def create_service_mcp_server(
    service_name: str,
    client: httpx.AsyncClient,
    auth: "AuthProvider | None" = None,
) -> ProxyBackedMCPServer:
    provider = ProxyEndpointProvider(service_name, client)
    return ProxyBackedMCPServer(service_name, provider, client, auth=auth)


def _parse_json_input(raw: str) -> Any:
    if raw == "null":
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw
