from __future__ import annotations

import json
from typing import Any
from collections.abc import AsyncIterable, AsyncIterator

import httpx

from flux.agents.events import is_terminal_state


async def _iter_sse_data_frames(lines: AsyncIterable[str]) -> AsyncIterator[dict]:
    """Yield one decoded JSON object per SSE ``data:`` frame.

    Per the SSE spec a single event may span multiple ``data:`` lines; the
    receiver joins them with ``\\n``. A blank line terminates the event. Flux
    streams pretty-printed JSON, so buffering is required; parsing each line
    individually produces ``Expecting property name`` errors on ``data: {``.
    """
    buf: list[str] = []
    async for raw in lines:
        if raw == "" or raw is None:
            if buf:
                try:
                    yield json.loads("\n".join(buf))
                except json.JSONDecodeError:
                    pass
                buf = []
            continue
        if raw.startswith("data:"):
            chunk = raw[5:]
            if chunk.startswith(" "):
                chunk = chunk[1:]
            buf.append(chunk)
    if buf:
        try:
            yield json.loads("\n".join(buf))
        except json.JSONDecodeError:
            pass


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

    def _resume_url(self, namespace: str, workflow_name: str, execution_id: str) -> str:
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
        body: dict[str, Any] = {"agent": agent_name}

        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream(
                "POST",
                url,
                json=body,
                headers=self._build_headers(),
            ) as response:
                response.raise_for_status()
                execution_id = None

                async for data in _iter_sse_data_frames(response.aiter_lines()):
                    if execution_id is None and "execution_id" in data:
                        execution_id = data["execution_id"]
                    yield execution_id, data
                    # Flux keeps the SSE open while the workflow is running.
                    # PAUSED / COMPLETED / FAILED / CANCELLED all mean "no more
                    # events until the caller initiates a new action", so we
                    # close the stream here rather than block indefinitely.
                    if is_terminal_state(data):
                        break

    async def resume(
        self,
        execution_id: str,
        message: str | None = None,
        namespace: str = "agents",
        workflow_name: str = "agent_chat",
        payload: dict[str, Any] | None = None,
    ) -> AsyncIterator[dict]:
        url = self._resume_url(namespace, workflow_name, execution_id)
        if payload is not None:
            resume_input: dict[str, Any] = payload
        elif message is not None:
            resume_input = {"message": message}
        else:
            resume_input = {}

        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream(
                "POST",
                url,
                json=resume_input,
                headers=self._build_headers(),
            ) as response:
                response.raise_for_status()

                async for data in _iter_sse_data_frames(response.aiter_lines()):
                    yield data
                    if is_terminal_state(data):
                        break

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
