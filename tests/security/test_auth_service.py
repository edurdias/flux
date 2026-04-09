from unittest.mock import MagicMock

import pytest

from flux.security.auth_service import AuthService
from flux.security.identity import FluxIdentity
from flux.security.config import AuthConfig, OIDCConfig, APIKeyAuthConfig
from flux.security.models import RoleModel
from flux.security.errors import AuthenticationError


class TestAuthServiceAuthenticate:
    @pytest.mark.asyncio
    async def test_authenticate_disabled_returns_anonymous(self):
        config = AuthConfig()
        service = AuthService(config=config, session_factory=MagicMock())
        identity = await service.authenticate(None)
        assert identity.subject == "anonymous"
        assert "admin" in identity.roles

    @pytest.mark.asyncio
    async def test_authenticate_no_token_raises(self):
        config = AuthConfig(oidc=OIDCConfig(enabled=True, issuer="https://x.com", audience="flux"))
        service = AuthService(config=config, session_factory=MagicMock())
        with pytest.raises(AuthenticationError):
            await service.authenticate(None)


class TestAuthServiceIsAuthorized:
    @pytest.fixture
    def service(self):
        config = AuthConfig(api_keys=APIKeyAuthConfig(enabled=True))
        session = MagicMock()
        role = MagicMock(spec=RoleModel)
        role.name = "operator"
        role.permissions = [
            "workflow:*:run",
            "workflow:*:read",
            "workflow:*:register",
            "workflow:*:task:*:execute",
            "schedule:*",
            "execution:*",
        ]
        session.query.return_value.filter_by.return_value.first.return_value = role
        return AuthService(config=config, session_factory=lambda: session)

    @pytest.mark.asyncio
    async def test_is_authorized_with_matching_permission(self, service):
        identity = FluxIdentity(subject="alice@acme.com", roles=frozenset({"operator"}))
        result = await service.is_authorized(identity, "workflow:report:run")
        assert result is True

    @pytest.mark.asyncio
    async def test_is_authorized_denied(self, service):
        identity = FluxIdentity(subject="alice@acme.com", roles=frozenset({"operator"}))
        result = await service.is_authorized(identity, "admin:secrets:manage")
        assert result is False

    @pytest.mark.asyncio
    async def test_is_authorized_admin_matches_all(self):
        config = AuthConfig(api_keys=APIKeyAuthConfig(enabled=True))
        session = MagicMock()
        role = MagicMock(spec=RoleModel)
        role.name = "admin"
        role.permissions = ["*"]
        session.query.return_value.filter_by.return_value.first.return_value = role
        service = AuthService(config=config, session_factory=lambda: session)
        identity = FluxIdentity(subject="admin@acme.com", roles=frozenset({"admin"}))
        result = await service.is_authorized(identity, "anything:at:all")
        assert result is True

    @pytest.mark.asyncio
    async def test_identity_with_unknown_role(self):
        config = AuthConfig(api_keys=APIKeyAuthConfig(enabled=True))
        session = MagicMock()
        session.query.return_value.filter_by.return_value.first.return_value = None
        service = AuthService(config=config, session_factory=lambda: session)
        identity = FluxIdentity(subject="test", roles=frozenset({"nonexistent"}))
        result = await service.is_authorized(identity, "anything")
        assert result is False

    @pytest.mark.asyncio
    async def test_builtin_role_fallback_without_db(self):
        config = AuthConfig(api_keys=APIKeyAuthConfig(enabled=True))
        session = MagicMock()
        session.query.return_value.filter_by.return_value.first.return_value = None
        service = AuthService(config=config, session_factory=lambda: session)
        identity = FluxIdentity(subject="admin@test", roles=frozenset({"admin"}))
        result = await service.is_authorized(identity, "anything:at:all")
        assert result is True
