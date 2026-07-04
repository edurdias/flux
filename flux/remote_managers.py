"""Remote config/secret managers that call the Flux server API.

Used by workers to resolve config_requests and secret_requests at task
runtime, replacing direct DB access.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

import httpx

from flux.config_manager import ConfigManager
from flux.secret_managers import SecretManager


_REMOTE_CONFIG: ContextVar[ConfigManager | None] = ContextVar("_remote_config", default=None)
_REMOTE_SECRET: ContextVar[SecretManager | None] = ContextVar("_remote_secret", default=None)
_REMOTE_APPROVALS: ContextVar[Any | None] = ContextVar("_remote_approvals", default=None)


class RemoteConfigManager(ConfigManager):
    """Resolves configs via the Flux server's batch endpoint."""

    def __init__(self, server_url: str, auth_token: str | None = None) -> None:
        self._server_url = server_url.rstrip("/")
        self._auth_token = auth_token
        self._client = httpx.AsyncClient(timeout=30)

    def _headers(self) -> dict[str, str]:
        h: dict[str, str] = {}
        if self._auth_token:
            h["Authorization"] = f"Bearer {self._auth_token}"
        return h

    async def get(self, config_requests: list[str]) -> dict[str, Any]:
        resp = await self._client.post(
            f"{self._server_url}/admin/configs/batch",
            json=config_requests,
            headers=self._headers(),
        )
        if resp.status_code == 404:
            raise ValueError(resp.json().get("detail", "Configs not found"))
        resp.raise_for_status()
        return resp.json()

    async def aclose(self) -> None:
        await self._client.aclose()

    def save(self, name: str, value: Any) -> None:
        raise NotImplementedError("RemoteConfigManager is read-only on the worker")

    def remove(self, name: str) -> None:
        raise NotImplementedError("RemoteConfigManager is read-only on the worker")

    def all(self) -> list[str]:
        raise NotImplementedError("RemoteConfigManager requires async context")


class RemoteSecretManager(SecretManager):
    """Resolves secrets via the Flux server's batch endpoint."""

    def __init__(
        self,
        server_url: str,
        auth_token: str | None = None,
        worker_name: str | None = None,
        execution_id: str | None = None,
    ) -> None:
        self._server_url = server_url.rstrip("/")
        self._auth_token = auth_token
        self._worker_name = worker_name
        self._execution_id = execution_id
        self._client = httpx.AsyncClient(timeout=30)

    def _headers(self) -> dict[str, str]:
        h: dict[str, str] = {}
        if self._auth_token:
            h["Authorization"] = f"Bearer {self._auth_token}"
        return h

    async def get(self, secret_requests: list[str]) -> dict[str, Any]:
        if self._worker_name and self._execution_id:
            resp = await self._client.post(
                f"{self._server_url}/workers/{self._worker_name}/secrets/batch",
                json={"execution_id": self._execution_id, "names": secret_requests},
                headers=self._headers(),
            )
        else:
            resp = await self._client.post(
                f"{self._server_url}/admin/secrets/batch",
                json=secret_requests,
                headers=self._headers(),
            )
        if resp.status_code == 404:
            raise ValueError(resp.json().get("detail", "Secrets not found"))
        if resp.status_code == 403:
            raise ValueError(resp.json().get("detail", "Secret access denied"))
        resp.raise_for_status()
        return resp.json()

    async def aclose(self) -> None:
        await self._client.aclose()

    def save(self, name: str, value: Any) -> None:
        raise NotImplementedError("RemoteSecretManager is read-only on the worker")

    def remove(self, name: str) -> None:
        raise NotImplementedError("RemoteSecretManager is read-only on the worker")

    def all(self) -> list[str]:
        raise NotImplementedError("RemoteSecretManager requires async context")


class RemoteApprovalStore:
    """Approval gate operations via the Flux server's worker endpoints.

    The distributed counterpart of ``flux.approvals.LocalApprovalStore``:
    workers (and, via the parent worker's pipe, runner children) never read
    or write approval rows directly — the server owns the database.
    """

    def __init__(
        self,
        server_url: str,
        auth_token: str | None = None,
        worker_name: str | None = None,
    ) -> None:
        self._server_url = server_url.rstrip("/")
        self._auth_token = auth_token
        self._worker_name = worker_name
        self._client = httpx.AsyncClient(timeout=30)

    def _headers(self) -> dict[str, str]:
        h: dict[str, str] = {}
        if self._auth_token:
            h["Authorization"] = f"Bearer {self._auth_token}"
        return h

    async def get_by_call(self, execution_id: str, task_call_id: str):
        from flux.approvals import ApprovalSnapshot

        resp = await self._client.get(
            f"{self._server_url}/workers/{self._worker_name}/approvals"
            f"/{execution_id}/{task_call_id}",
            headers=self._headers(),
        )
        resp.raise_for_status()
        data = resp.json().get("approval")
        return ApprovalSnapshot.from_dict(data) if data else None

    async def register(self, ctx, task_call_id: str, task_name: str, awaiting_event) -> str:
        resp = await self._client.post(
            f"{self._server_url}/workers/{self._worker_name}/approvals/{ctx.execution_id}",
            json={"task_call_id": task_call_id, "task_name": task_name},
            headers=self._headers(),
        )
        resp.raise_for_status()
        status = resp.json().get("status", "cancelled")
        if status in ("created", "exists"):
            # The awaiting event reaches the server through the normal
            # checkpoint path (the pause checkpoint at the latest),
            # deduplicated by event id.
            ctx.events.append(awaiting_event)
        return status

    async def aclose(self) -> None:
        await self._client.aclose()


def set_remote_managers(
    config: ConfigManager | None = None,
    secret: SecretManager | None = None,
    approvals: Any | None = None,
) -> tuple:
    """Set remote managers for the current async context. Returns tokens for reset."""
    ct = _REMOTE_CONFIG.set(config)
    st = _REMOTE_SECRET.set(secret)
    at = _REMOTE_APPROVALS.set(approvals)
    return ct, st, at


def reset_remote_managers(tokens: tuple) -> None:
    ct, st, at = tokens
    _REMOTE_CONFIG.reset(ct)
    _REMOTE_SECRET.reset(st)
    _REMOTE_APPROVALS.reset(at)


def get_remote_config() -> ConfigManager | None:
    return _REMOTE_CONFIG.get()


def get_remote_secret() -> SecretManager | None:
    return _REMOTE_SECRET.get()


def get_remote_approvals() -> Any | None:
    return _REMOTE_APPROVALS.get()
