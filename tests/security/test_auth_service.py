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


class TestAuthServicePrincipalDependency:
    def test_auth_service_accepts_registry(self):
        from flux.security.auth_service import AuthService
        from flux.security.config import AuthConfig
        from flux.security.principals import PrincipalRegistry

        config = AuthConfig()
        registry = MagicMock(spec=PrincipalRegistry)
        service = AuthService(config=config, session_factory=MagicMock(), registry=registry)
        assert service._registry is registry

    def test_auth_service_no_service_account_methods(self):
        from flux.security.auth_service import AuthService

        assert not hasattr(AuthService, "list_service_accounts")
        assert not hasattr(AuthService, "create_service_account")
        assert not hasattr(AuthService, "update_service_account")
        assert not hasattr(AuthService, "delete_service_account")


class TestAuthServicePrincipalCRUD:
    @pytest.fixture
    def registry(self):
        from flux.security.principals import PrincipalRegistry

        return MagicMock(spec=PrincipalRegistry)

    @pytest.fixture
    def service(self, registry):
        from flux.security.auth_service import AuthService
        from flux.security.config import AuthConfig

        config = AuthConfig()
        return AuthService(config=config, session_factory=MagicMock(), registry=registry)

    @pytest.mark.asyncio
    async def test_create_principal_delegates_to_registry(self, service, registry):
        from flux.security.principals import PrincipalModel

        mock_principal = MagicMock(spec=PrincipalModel)
        mock_principal.id = "abc123"
        registry.create.return_value = mock_principal

        result = await service.create_principal(
            type="service_account",
            subject="svc-test",
            external_issuer="flux",
            display_name="Test SA",
            roles=["operator"],
        )
        registry.create.assert_called_once()
        assert result is mock_principal

    @pytest.mark.asyncio
    async def test_list_principals_delegates_to_registry(self, service, registry):
        registry.list_all = MagicMock(return_value=[])
        result = await service.list_principals()
        assert result == []

    @pytest.mark.asyncio
    async def test_enable_principal_calls_set_enabled(self, service, registry):
        await service.enable_principal("abc123")
        registry.set_enabled.assert_called_once_with("abc123", True)

    @pytest.mark.asyncio
    async def test_disable_principal_calls_set_enabled(self, service, registry):
        await service.disable_principal("abc123")
        registry.set_enabled.assert_called_once_with("abc123", False)

    @pytest.mark.asyncio
    async def test_grant_role_calls_assign_role(self, service, registry):
        await service.grant_role("abc123", "operator", granted_by="admin")
        registry.assign_role.assert_called_once_with("abc123", "operator", assigned_by="admin")

    @pytest.mark.asyncio
    async def test_revoke_role_calls_registry(self, service, registry):
        await service.revoke_role("abc123", "operator")
        registry.revoke_role.assert_called_once_with("abc123", "operator")


def test_internal_provider_module_removed():
    import importlib

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("flux.security.providers.internal")
