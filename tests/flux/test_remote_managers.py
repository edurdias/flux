"""Unit tests for RemoteConfigManager / RemoteSecretManager."""

from __future__ import annotations

import pytest

from flux.remote_managers import RemoteConfigManager, RemoteSecretManager


@pytest.mark.asyncio
async def test_remote_config_manager_aclose_closes_underlying_client():
    manager = RemoteConfigManager("http://flux.test", auth_token="t")
    assert manager._client.is_closed is False

    await manager.aclose()

    assert manager._client.is_closed is True


@pytest.mark.asyncio
async def test_remote_secret_manager_aclose_closes_underlying_client():
    manager = RemoteSecretManager("http://flux.test", auth_token="t")
    assert manager._client.is_closed is False

    await manager.aclose()

    assert manager._client.is_closed is True
