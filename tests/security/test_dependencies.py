from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch
from fastapi import HTTPException

from flux.security.dependencies import get_identity, require_permission
from flux.security.identity import FluxIdentity, ANONYMOUS
from flux.security.errors import AuthenticationError


class TestGetIdentity:
    @pytest.mark.asyncio
    async def test_returns_anonymous_when_no_auth_service(self):
        with patch(
            "flux.security.dependencies._get_auth_service", return_value=None
        ):
            identity = await get_identity(authorization=None)
        assert identity.subject == "anonymous"

    @pytest.mark.asyncio
    async def test_returns_anonymous_when_disabled(self):
        mock_auth_service = AsyncMock()
        mock_auth_service.authenticate.return_value = ANONYMOUS
        with patch(
            "flux.security.dependencies._get_auth_service",
            return_value=mock_auth_service,
        ):
            identity = await get_identity(authorization=None)
        assert identity.subject == "anonymous"

    @pytest.mark.asyncio
    async def test_raises_401_on_auth_error(self):
        mock_auth_service = AsyncMock()
        mock_auth_service.authenticate.side_effect = AuthenticationError(
            "Token required"
        )
        with patch(
            "flux.security.dependencies._get_auth_service",
            return_value=mock_auth_service,
        ):
            with pytest.raises(HTTPException) as exc_info:
                await get_identity(authorization=None)
            assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_extracts_bearer_token(self):
        expected = FluxIdentity(
            subject="alice@acme.com", roles=frozenset({"operator"})
        )
        mock_auth_service = AsyncMock()
        mock_auth_service.authenticate.return_value = expected
        with patch(
            "flux.security.dependencies._get_auth_service",
            return_value=mock_auth_service,
        ):
            identity = await get_identity(authorization="Bearer some-jwt-token")
        mock_auth_service.authenticate.assert_called_once_with("some-jwt-token")
        assert identity.subject == "alice@acme.com"

    @pytest.mark.asyncio
    async def test_passes_none_when_no_bearer_prefix(self):
        mock_auth_service = AsyncMock()
        mock_auth_service.authenticate.return_value = ANONYMOUS
        with patch(
            "flux.security.dependencies._get_auth_service",
            return_value=mock_auth_service,
        ):
            await get_identity(authorization="Basic abc123")
        mock_auth_service.authenticate.assert_called_once_with(None)


class TestRequirePermission:
    @pytest.mark.asyncio
    async def test_returns_identity_when_no_auth_service(self):
        dep = require_permission("workflow:*:read")
        with patch(
            "flux.security.dependencies._get_auth_service", return_value=None
        ):
            identity = await dep(identity=ANONYMOUS)
        assert identity.subject == "anonymous"

    @pytest.mark.asyncio
    async def test_returns_identity_when_authorized(self):
        mock_auth_service = AsyncMock()
        mock_auth_service.is_authorized.return_value = True
        dep = require_permission("workflow:*:read")
        identity = FluxIdentity(
            subject="alice@acme.com", roles=frozenset({"viewer"})
        )
        with patch(
            "flux.security.dependencies._get_auth_service",
            return_value=mock_auth_service,
        ):
            result = await dep(identity=identity)
        assert result.subject == "alice@acme.com"

    @pytest.mark.asyncio
    async def test_raises_403_when_not_authorized(self):
        mock_auth_service = AsyncMock()
        mock_auth_service.is_authorized.return_value = False
        dep = require_permission("admin:secrets:manage")
        identity = FluxIdentity(
            subject="bob@acme.com", roles=frozenset({"viewer"})
        )
        with patch(
            "flux.security.dependencies._get_auth_service",
            return_value=mock_auth_service,
        ):
            with pytest.raises(HTTPException) as exc_info:
                await dep(identity=identity)
            assert exc_info.value.status_code == 403
            assert "admin:secrets:manage" in exc_info.value.detail
