from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


@dataclass
class BearerAuthConfig:
    token: str | None = None
    secret: str | None = None
    provider: Callable[[], str | Awaitable[str]] | None = None


@dataclass
class OAuthConfig:
    scopes: list[str] | None = None
    client_id: str | None = None
    client_secret: str | None = None
    client_name: str | None = None
    token_storage: Any | None = None
    callback_port: int | None = None


def bearer(
    token: str | None = None,
    *,
    secret: str | None = None,
    provider: Callable[[], str | Awaitable[str]] | None = None,
) -> BearerAuthConfig:
    if token is None and secret is None and provider is None:
        raise ValueError("bearer() requires at least one of: token, secret, or provider.")
    return BearerAuthConfig(token=token, secret=secret, provider=provider)


def oauth(
    *,
    scopes: list[str] | None = None,
    client_id: str | None = None,
    client_secret: str | None = None,
    client_name: str | None = None,
    token_storage: Any | None = None,
    callback_port: int | None = None,
) -> OAuthConfig:
    return OAuthConfig(
        scopes=scopes,
        client_id=client_id,
        client_secret=client_secret,
        client_name=client_name,
        token_storage=token_storage,
        callback_port=callback_port,
    )
