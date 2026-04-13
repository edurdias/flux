from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import httpx
from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse


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
        self._cache_time: float = 0.0

    async def _refresh_cache(self) -> None:
        async with self._lock:
            if self._endpoints and (time.monotonic() - self._cache_time) < self.cache_ttl:
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
            self._cache_time = 0.0
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
        if self._cache_time == 0.0:
            return 0.0
        return time.monotonic() - self._cache_time

    async def close(self) -> None:
        await self._client.aclose()


def create_standalone_app(
    service_name: str,
    server_url: str,
    cache_ttl: int = 60,
) -> FastAPI:
    app = FastAPI(title=f"Flux Service: {service_name}")
    proxy = StandaloneServiceProxy(service_name, server_url, cache_ttl)

    @app.on_event("shutdown")
    async def shutdown():
        await proxy.close()

    @app.get("/health")
    async def health():
        return {
            "service": proxy.service_name,
            "endpoints": proxy.endpoint_count,
            "cache_age": round(proxy.cache_age, 1),
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

        path = f"/workflows/{ref.namespace}/{ref.name}/run/{mode}"
        auth_headers = {}
        if "authorization" in request.headers:
            auth_headers["authorization"] = request.headers["authorization"]

        params: dict[str, str] = {}
        if detailed:
            params["detailed"] = "true"
        if version:
            params["version"] = version

        body = await request.json() if await request.body() else None

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

        path = f"/workflows/{ref.namespace}/{ref.name}/resume/{execution_id}/{mode}"
        auth_headers = {}
        if "authorization" in request.headers:
            auth_headers["authorization"] = request.headers["authorization"]

        body = await request.json() if await request.body() else None

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
