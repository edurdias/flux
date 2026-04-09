from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from flux.security.providers.api_key import APIKeyProvider
from flux.security.models import APIKeyModel, ServiceAccountModel


class TestAPIKeyProvider:
    @pytest.fixture
    def mock_session(self):
        return MagicMock()

    @pytest.fixture
    def provider(self, mock_session):
        return APIKeyProvider(session_factory=lambda: mock_session)

    @pytest.mark.asyncio
    async def test_authenticate_valid_key(self, provider, mock_session):
        key_plaintext = "flux_sk_abc123def456ghi789"
        key_hash = hashlib.sha256(key_plaintext.encode()).hexdigest()

        mock_key = MagicMock(spec=APIKeyModel)
        mock_key.key_hash = key_hash
        mock_key.expires_at = None
        mock_key.service_account = MagicMock(spec=ServiceAccountModel)
        mock_key.service_account.name = "svc-pipeline"
        mock_key.service_account.roles = ["operator"]

        mock_session.query.return_value.filter.return_value.first.return_value = mock_key

        identity = await provider.authenticate(key_plaintext)

        assert identity is not None
        assert identity.subject == "svc-pipeline"
        assert "operator" in identity.roles

    @pytest.mark.asyncio
    async def test_authenticate_expired_key(self, provider, mock_session):
        key_plaintext = "flux_sk_abc123def456ghi789"
        key_hash = hashlib.sha256(key_plaintext.encode()).hexdigest()

        mock_key = MagicMock(spec=APIKeyModel)
        mock_key.key_hash = key_hash
        mock_key.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
        mock_key.service_account = MagicMock(spec=ServiceAccountModel)

        mock_session.query.return_value.filter.return_value.first.return_value = mock_key

        identity = await provider.authenticate(key_plaintext)

        assert identity is None

    @pytest.mark.asyncio
    async def test_authenticate_unknown_key(self, provider, mock_session):
        mock_session.query.return_value.filter.return_value.first.return_value = None

        identity = await provider.authenticate("flux_sk_unknown")

        assert identity is None
