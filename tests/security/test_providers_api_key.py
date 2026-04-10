from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from flux.security.providers.api_key import APIKeyProvider
from flux.security.models import APIKeyModel
from flux.security.principals import PrincipalModel, PrincipalRegistry


class TestAPIKeyProvider:
    @pytest.fixture
    def mock_session(self):
        return MagicMock()

    @pytest.fixture
    def mock_registry(self):
        return MagicMock(spec=PrincipalRegistry)

    @pytest.fixture
    def provider(self, mock_session, mock_registry):
        return APIKeyProvider(session_factory=lambda: mock_session, registry=mock_registry)

    def _make_key_mock(self, principal_id: str = "p1", expires_at=None, name: str = "default"):
        key = MagicMock(spec=APIKeyModel)
        key.principal_id = principal_id
        key.name = name
        key.expires_at = expires_at
        return key

    def _make_principal_mock(self, type_: str = "service_account", enabled: bool = True):
        p = MagicMock(spec=PrincipalModel)
        p.id = "p1"
        p.type = type_
        p.subject = "svc-pipeline"
        p.enabled = enabled
        return p

    @pytest.mark.asyncio
    async def test_authenticate_valid_key(self, provider, mock_session, mock_registry):
        key_plaintext = "flux_sk_abc123def456ghi789jkl012"
        key_hash = hashlib.sha256(key_plaintext.encode()).hexdigest()

        mock_key = self._make_key_mock()
        mock_key.key_hash = key_hash
        mock_session.query.return_value.filter.return_value.first.return_value = mock_key

        mock_principal = self._make_principal_mock()
        mock_registry.get.return_value = mock_principal
        mock_registry.get_roles.return_value = ["operator"]

        identity = await provider.authenticate(key_plaintext)

        assert identity is not None
        assert identity.subject == "svc-pipeline"
        assert "operator" in identity.roles
        assert identity.metadata["issuer"] == "flux"
        assert identity.metadata["principal_id"] == "p1"
        assert identity.metadata["key_name"] == "default"

    @pytest.mark.asyncio
    async def test_authenticate_updates_last_seen(self, provider, mock_session, mock_registry):
        key_plaintext = "flux_sk_abc123def456ghi789jkl012"
        mock_key = self._make_key_mock()
        mock_session.query.return_value.filter.return_value.first.return_value = mock_key

        mock_principal = self._make_principal_mock()
        mock_registry.get.return_value = mock_principal
        mock_registry.get_roles.return_value = []

        await provider.authenticate(key_plaintext)
        mock_registry.update_last_seen.assert_called_once_with("p1")

    @pytest.mark.asyncio
    async def test_authenticate_expired_key_returns_none(
        self,
        provider,
        mock_session,
        mock_registry,
    ):
        key_plaintext = "flux_sk_abc123def456ghi789jkl012"
        mock_key = self._make_key_mock(expires_at=datetime.now(timezone.utc) - timedelta(days=1))
        mock_session.query.return_value.filter.return_value.first.return_value = mock_key

        identity = await provider.authenticate(key_plaintext)
        assert identity is None

    @pytest.mark.asyncio
    async def test_authenticate_unknown_key_returns_none(
        self,
        provider,
        mock_session,
        mock_registry,
    ):
        mock_session.query.return_value.filter.return_value.first.return_value = None
        identity = await provider.authenticate("flux_sk_unknown")
        assert identity is None

    @pytest.mark.asyncio
    async def test_authenticate_disabled_principal_returns_none(
        self,
        provider,
        mock_session,
        mock_registry,
    ):
        key_plaintext = "flux_sk_abc123def456ghi789jkl012"
        mock_key = self._make_key_mock()
        mock_session.query.return_value.filter.return_value.first.return_value = mock_key

        mock_principal = self._make_principal_mock(enabled=False)
        mock_registry.get.return_value = mock_principal

        identity = await provider.authenticate(key_plaintext)
        assert identity is None

    @pytest.mark.asyncio
    async def test_authenticate_wrong_principal_type_returns_none(
        self,
        provider,
        mock_session,
        mock_registry,
    ):
        key_plaintext = "flux_sk_abc123def456ghi789jkl012"
        mock_key = self._make_key_mock()
        mock_session.query.return_value.filter.return_value.first.return_value = mock_key

        mock_principal = self._make_principal_mock(type_="user")
        mock_registry.get.return_value = mock_principal

        identity = await provider.authenticate(key_plaintext)
        assert identity is None
