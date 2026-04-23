from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WorkflowRef:
    namespace: str
    name: str


class StandaloneServiceProxy:
    def __init__(self, service_name: str, server_url: str, cache_ttl: int = 60):
        self.service_name = service_name
        self.server_url = server_url.rstrip("/")
        self.cache_ttl = cache_ttl
        self._client = httpx.AsyncClient(base_url=self.server_url, timeout=30.0)
        self._lock = asyncio.Lock()
        self._endpoints: dict[str, WorkflowRef] = {}
        self._cache_time: float | None = None

    async def _refresh_cache(self) -> None:
        async with self._lock:
            if (
                self._cache_time is not None
                and (time.monotonic() - self._cache_time) < self.cache_ttl
            ):
                return
            response = await self._client.get(f"/services/{self.service_name}")
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    raise KeyError(f"Service '{self.service_name}' not found on server") from exc
                raise ConnectionError(
                    f"Failed to refresh service cache: {exc.response.status_code}",
                ) from exc
            data = response.json()
            endpoints: dict[str, WorkflowRef] = {}
            for ep in data.get("endpoints", []):
                if isinstance(ep, dict):
                    name = ep.get("name")
                    namespace = ep.get("namespace")
                    if name and namespace:
                        endpoints[name] = WorkflowRef(namespace=namespace, name=name)
                elif isinstance(ep, str):
                    parts = ep.split("/", 1)
                    if len(parts) == 2:
                        endpoints[parts[1]] = WorkflowRef(namespace=parts[0], name=parts[1])
            self._endpoints = endpoints
            self._cache_time = time.monotonic()

    async def resolve(self, workflow_name: str) -> WorkflowRef:
        if workflow_name not in self._endpoints:
            self._cache_time = None
            await self._refresh_cache()
        if workflow_name not in self._endpoints:
            raise KeyError(f"Workflow '{workflow_name}' not found in service '{self.service_name}'")
        return self._endpoints[workflow_name]

    async def forward(
        self,
        method: str,
        path: str,
        auth_headers: dict[str, str] | None = None,
        json_body: dict | None = None,
        params: dict | None = None,
    ) -> httpx.Response:
        headers = dict(auth_headers) if auth_headers else {}
        return await self._client.request(
            method,
            path,
            headers=headers,
            json=json_body,
            params=params,
        )

    @property
    def endpoint_count(self) -> int:
        return len(self._endpoints)

    @property
    def cache_age(self) -> float:
        if self._cache_time is None:
            return 0.0
        return time.monotonic() - self._cache_time

    async def close(self) -> None:
        await self._client.aclose()


class MCPRouteMiddleware:
    """ASGI middleware that intercepts ``/mcp`` and ``/.well-known/`` requests,
    forwarding them to the FastMCP ASGI app before FastAPI's catch-all
    ``/{workflow_name}`` route can match them.

    Registered via ``app.add_middleware(MCPRouteMiddleware, mcp_app=...)``.
    """

    def __init__(self, app: Any, *, mcp_app: Any):
        self.app = app
        self.mcp_app = mcp_app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] == "http" and self._is_mcp_route(scope.get("path", "")):
            await self.mcp_app(scope, receive, send)
        else:
            await self.app(scope, receive, send)

    @staticmethod
    def _is_mcp_route(path: str) -> bool:
        return path == "/mcp" or path.startswith("/mcp/") or path.startswith("/.well-known/")


def create_standalone_app(
    service_name: str,
    server_url: str,
    cache_ttl: int = 60,
    enable_mcp: bool = False,
    mcp_auth: Any | None = None,
) -> FastAPI:
    proxy = StandaloneServiceProxy(service_name, server_url, cache_ttl)

    mcp_server = None
    mcp_http_app = None

    if enable_mcp:
        from flux.service_mcp import create_service_mcp_server

        mcp_server = create_service_mcp_server(service_name, proxy._client, auth=mcp_auth)
        mcp_http_app = mcp_server.mcp.http_app(path="/mcp")

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
        try:
            if mcp_server is not None and mcp_http_app is not None:
                async with mcp_http_app.router.lifespan_context(mcp_http_app):
                    await _init_mcp(mcp_server)
                    refresh_task = asyncio.create_task(
                        _mcp_refresh_loop(mcp_server, cache_ttl),
                    )
                    yield
                    refresh_task.cancel()
            else:
                yield
        finally:
            await proxy.close()

    app = FastAPI(title=f"Flux Service: {service_name}", lifespan=lifespan)

    if enable_mcp and mcp_http_app is not None:
        app.add_middleware(MCPRouteMiddleware, mcp_app=mcp_http_app)

    @app.get("/health")
    async def health():
        return {
            "service": proxy.service_name,
            "endpoints": proxy.endpoint_count,
            "cache_age": round(proxy.cache_age, 1),
            "mcp_enabled": enable_mcp,
        }

    @app.post("/{workflow_name}")
    async def run_workflow(
        request: Request,
        workflow_name: str,
        mode: str = Query("sync"),
        detailed: bool = Query(False),
        version: str | None = Query(None),
    ):
        if mode not in ("sync", "async"):
            return JSONResponse(
                status_code=400,
                content={"detail": "Standalone proxy only supports 'sync' and 'async' modes"},
            )

        try:
            ref = await proxy.resolve(workflow_name)
        except KeyError as e:
            return JSONResponse(status_code=404, content={"detail": str(e)})
        except ConnectionError:
            return JSONResponse(
                status_code=502,
                content={"detail": f"Cannot reach Flux server at {proxy.server_url}"},
            )

        path = f"/workflows/{ref.namespace}/{ref.name}/run/{mode}"
        auth_headers = {}
        if "authorization" in request.headers:
            auth_headers["authorization"] = request.headers["authorization"]

        params: dict[str, str] = {}
        if detailed:
            params["detailed"] = "true"
        if version:
            params["version"] = version

        try:
            body = await request.json() if await request.body() else None
        except Exception:
            return JSONResponse(status_code=400, content={"detail": "Invalid JSON body"})

        try:
            response = await proxy.forward("POST", path, auth_headers, body, params or None)
        except httpx.ConnectError:
            return JSONResponse(
                status_code=502,
                content={"detail": "Cannot connect to Flux server"},
            )
        except httpx.TimeoutException:
            return JSONResponse(status_code=504, content={"detail": "Flux server timeout"})

        return JSONResponse(status_code=response.status_code, content=response.json())

    @app.post("/{workflow_name}/resume/{execution_id}")
    async def resume_workflow(
        request: Request,
        workflow_name: str,
        execution_id: str,
        mode: str = Query("sync"),
    ):
        try:
            ref = await proxy.resolve(workflow_name)
        except KeyError as e:
            return JSONResponse(status_code=404, content={"detail": str(e)})
        except ConnectionError:
            return JSONResponse(
                status_code=502,
                content={"detail": f"Cannot reach Flux server at {proxy.server_url}"},
            )

        path = f"/workflows/{ref.namespace}/{ref.name}/resume/{execution_id}/{mode}"
        auth_headers = {}
        if "authorization" in request.headers:
            auth_headers["authorization"] = request.headers["authorization"]

        try:
            body = await request.json() if await request.body() else None
        except Exception:
            return JSONResponse(status_code=400, content={"detail": "Invalid JSON body"})

        try:
            response = await proxy.forward("POST", path, auth_headers, body)
        except httpx.ConnectError:
            return JSONResponse(
                status_code=502,
                content={"detail": "Cannot connect to Flux server"},
            )
        except httpx.TimeoutException:
            return JSONResponse(status_code=504, content={"detail": "Flux server timeout"})

        return JSONResponse(status_code=response.status_code, content=response.json())

    @app.get("/{workflow_name}/status/{execution_id}")
    async def workflow_status(
        request: Request,
        workflow_name: str,
        execution_id: str,
    ):
        try:
            ref = await proxy.resolve(workflow_name)
        except KeyError as e:
            return JSONResponse(status_code=404, content={"detail": str(e)})

        path = f"/workflows/{ref.namespace}/{ref.name}/status/{execution_id}"
        auth_headers = {}
        if "authorization" in request.headers:
            auth_headers["authorization"] = request.headers["authorization"]

        try:
            response = await proxy.forward("GET", path, auth_headers)
        except httpx.ConnectError:
            return JSONResponse(
                status_code=502,
                content={"detail": "Cannot connect to Flux server"},
            )
        except httpx.TimeoutException:
            return JSONResponse(status_code=504, content={"detail": "Flux server timeout"})

        return JSONResponse(status_code=response.status_code, content=response.json())

    return app


async def _init_mcp(mcp_server: Any) -> None:
    try:
        endpoints = await mcp_server.provider.get_endpoints()
        mcp_server._generate_tools(endpoints)
    except Exception as e:
        logger.warning(
            "Failed to initialize MCP tools on startup: %s. "
            "MCP endpoint mounted but has no tools. "
            "Restart the service once the Flux server is available.",
            e,
        )


async def _mcp_refresh_loop(mcp_server: Any, interval: int) -> None:
    while True:
        await asyncio.sleep(interval)
        try:
            await mcp_server.refresh()
        except Exception:
            pass
