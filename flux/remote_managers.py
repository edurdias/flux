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

    def save(self, name: str, value: Any) -> None:
        raise NotImplementedError("RemoteConfigManager is read-only on the worker")

    def remove(self, name: str) -> None:
        raise NotImplementedError("RemoteConfigManager is read-only on the worker")

    def all(self) -> list[str]:
        raise NotImplementedError("RemoteConfigManager requires async context")


class RemoteSecretManager(SecretManager):
    """Resolves secrets via the Flux server's batch endpoint."""

    def __init__(self, server_url: str, auth_token: str | None = None) -> None:
        self._server_url = server_url.rstrip("/")
        self._auth_token = auth_token
        self._client = httpx.AsyncClient(timeout=30)

    def _headers(self) -> dict[str, str]:
        h: dict[str, str] = {}
        if self._auth_token:
            h["Authorization"] = f"Bearer {self._auth_token}"
        return h

    async def get(self, secret_requests: list[str]) -> dict[str, Any]:
        resp = await self._client.post(
            f"{self._server_url}/admin/secrets/batch",
            json=secret_requests,
            headers=self._headers(),
        )
        if resp.status_code == 404:
            raise ValueError(resp.json().get("detail", "Secrets not found"))
        resp.raise_for_status()
        return resp.json()

    def save(self, name: str, value: Any) -> None:
        raise NotImplementedError("RemoteSecretManager is read-only on the worker")

    def remove(self, name: str) -> None:
        raise NotImplementedError("RemoteSecretManager is read-only on the worker")

    def all(self) -> list[str]:
        raise NotImplementedError("RemoteSecretManager requires async context")


def set_remote_managers(
    config: ConfigManager | None = None,
    secret: SecretManager | None = None,
) -> tuple:
    """Set remote managers for the current async context. Returns tokens for reset."""
    ct = _REMOTE_CONFIG.set(config)
    st = _REMOTE_SECRET.set(secret)
    return ct, st


def reset_remote_managers(tokens: tuple) -> None:
    ct, st = tokens
    _REMOTE_CONFIG.reset(ct)
    _REMOTE_SECRET.reset(st)


def get_remote_config() -> ConfigManager | None:
    return _REMOTE_CONFIG.get()


def get_remote_secret() -> SecretManager | None:
    return _REMOTE_SECRET.get()
