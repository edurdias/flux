from __future__ import annotations

import json
from typing import Any, AsyncIterator

import httpx


class FluxClient:
    def __init__(
        self,
        server_url: str,
        token: str | None = None,
    ):
        self.server_url = server_url.rstrip("/")
        self._token = token

    def _build_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _start_url(self, namespace: str, workflow_name: str) -> str:
        return f"{self.server_url}/workflows/{namespace}/{workflow_name}/run/stream"

    def _resume_url(
        self, namespace: str, workflow_name: str, execution_id: str
    ) -> str:
        return (
            f"{self.server_url}/workflows/{namespace}/{workflow_name}"
            f"/resume/{execution_id}/stream"
        )

    async def start_agent(
        self,
        agent_name: str,
        namespace: str = "agents",
        workflow_name: str = "agent_chat",
    ) -> AsyncIterator[tuple[str | None, dict]]:
        url = self._start_url(namespace, workflow_name)
        payload = {"input": json.dumps({"agent": agent_name})}

        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream(
                "POST", url, json=payload, headers=self._build_headers()
            ) as response:
                response.raise_for_status()
                execution_id = None

                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    if line.startswith("data: "):
                        data = json.loads(line[6:])
                        if execution_id is None and "execution_id" in data:
                            execution_id = data["execution_id"]
                        yield execution_id, data

    async def resume(
        self,
        execution_id: str,
        message: str,
        namespace: str = "agents",
        workflow_name: str = "agent_chat",
    ) -> AsyncIterator[dict]:
        url = self._resume_url(namespace, workflow_name, execution_id)
        payload = {"input": json.dumps({"message": message})}

        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream(
                "POST", url, json=payload, headers=self._build_headers()
            ) as response:
                response.raise_for_status()

                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    if line.startswith("data: "):
                        yield json.loads(line[6:])

    async def get_agent(self, name: str) -> dict:
        url = f"{self.server_url}/admin/agents/{name}"
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=self._build_headers())
            response.raise_for_status()
            return response.json()

    async def get_execution(self, execution_id: str) -> dict:
        url = f"{self.server_url}/executions/{execution_id}"
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=self._build_headers())
            response.raise_for_status()
            return response.json()
