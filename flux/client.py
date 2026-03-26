from __future__ import annotations

from typing import Any

import httpx

from flux.utils import get_logger

logger = get_logger(__name__)

DEFAULT_TIMEOUT: float = 10.0


class FluxClient:
    """Async HTTP client for the Flux REST API."""

    def __init__(self, server_url: str, timeout: float | None = DEFAULT_TIMEOUT):
        self.server_url = server_url
        self._http_client = httpx.AsyncClient(
            base_url=server_url,
            timeout=timeout,
        )

    async def close(self):
        await self._http_client.aclose()

    async def __aenter__(self) -> FluxClient:
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self.close()

    # --- Health ---

    async def health_check(self) -> dict[str, Any] | None:
        try:
            response = await self._http_client.get("/health")
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError:
            return None

    # --- Workflows ---

    async def list_workflows(self) -> list[dict[str, Any]]:
        response = await self._http_client.get("/workflows")
        response.raise_for_status()
        return response.json()

    async def get_workflow(self, name: str) -> dict[str, Any]:
        response = await self._http_client.get(f"/workflows/{name}")
        response.raise_for_status()
        return response.json()

    async def get_workflow_versions(self, name: str) -> list[dict[str, Any]]:
        response = await self._http_client.get(f"/workflows/{name}/versions")
        response.raise_for_status()
        return response.json()

    async def get_workflow_executions(self, name: str, limit: int = 30) -> list[dict[str, Any]]:
        response = await self._http_client.get(
            f"/workflows/{name}/executions",
            params={"limit": limit},
        )
        response.raise_for_status()
        return response.json()

    async def run_workflow(self, name: str, input_data: Any = None) -> dict[str, Any]:
        response = await self._http_client.post(f"/workflows/{name}/run/async", json=input_data)
        response.raise_for_status()
        return response.json()

    # --- Executions ---

    async def list_executions(
        self,
        workflow_name: str | None = None,
        state: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if workflow_name:
            params["workflow_name"] = workflow_name
        if state:
            params["state"] = state
        response = await self._http_client.get("/executions", params=params)
        response.raise_for_status()
        return response.json()

    async def get_execution(self, execution_id: str, detailed: bool = False) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if detailed:
            params["detailed"] = True
        response = await self._http_client.get(f"/executions/{execution_id}", params=params)
        response.raise_for_status()
        return response.json()

    async def cancel_execution(self, workflow_name: str, execution_id: str) -> dict[str, Any]:
        response = await self._http_client.get(f"/workflows/{workflow_name}/cancel/{execution_id}")
        response.raise_for_status()
        return response.json()

    async def resume_execution(
        self,
        workflow_name: str,
        execution_id: str,
        input_data: Any = None,
    ) -> dict[str, Any]:
        response = await self._http_client.post(
            f"/workflows/{workflow_name}/resume/{execution_id}/async",
            json=input_data,
        )
        response.raise_for_status()
        return response.json()

    # --- Workers ---

    async def list_workers(self) -> list[dict[str, Any]]:
        response = await self._http_client.get("/workers")
        response.raise_for_status()
        return response.json()

    async def get_worker(self, name: str) -> dict[str, Any]:
        response = await self._http_client.get(f"/workers/{name}")
        response.raise_for_status()
        return response.json()

    # --- Schedules ---

    async def list_schedules(self, active_only: bool = False) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        if active_only:
            params["active_only"] = True
        response = await self._http_client.get("/schedules", params=params)
        response.raise_for_status()
        return response.json()

    async def get_schedule(self, schedule_id: str) -> dict[str, Any]:
        response = await self._http_client.get(f"/schedules/{schedule_id}")
        response.raise_for_status()
        return response.json()

    async def get_schedule_history(self, schedule_id: str, limit: int = 20) -> list[dict[str, Any]]:
        response = await self._http_client.get(
            f"/schedules/{schedule_id}/history",
            params={"limit": limit},
        )
        response.raise_for_status()
        return response.json()

    async def update_schedule(self, schedule_id: str, data: dict[str, Any]) -> dict[str, Any]:
        response = await self._http_client.put(f"/schedules/{schedule_id}", json=data)
        response.raise_for_status()
        return response.json()

    async def pause_schedule(self, schedule_id: str) -> dict[str, Any]:
        response = await self._http_client.post(f"/schedules/{schedule_id}/pause")
        response.raise_for_status()
        return response.json()

    async def resume_schedule(self, schedule_id: str) -> dict[str, Any]:
        response = await self._http_client.post(f"/schedules/{schedule_id}/resume")
        response.raise_for_status()
        return response.json()

    async def delete_schedule(self, schedule_id: str) -> None:
        response = await self._http_client.delete(f"/schedules/{schedule_id}")
        response.raise_for_status()
