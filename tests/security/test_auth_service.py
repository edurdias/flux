from unittest.mock import MagicMock

import pytest

from flux.security.auth_service import AuthService, BUILT_IN_ROLES
from flux.security.identity import FluxIdentity
from flux.security.config import AuthConfig, OIDCConfig, APIKeyAuthConfig
from flux.security.models import RoleModel, APIKeyModel
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


def test_built_in_roles_are_4_segment():
    from flux.security.auth_service import BUILT_IN_ROLES

    assert "workflow:*:*:run" in BUILT_IN_ROLES["operator"]
    assert "workflow:*:*:read" in BUILT_IN_ROLES["operator"]
    assert "workflow:*:*:register" in BUILT_IN_ROLES["operator"]
    assert "workflow:*:*:task:*:execute" in BUILT_IN_ROLES["operator"]
    assert "workflow:*:*:read" in BUILT_IN_ROLES["viewer"]
    # Regression: old 3-segment entries should be gone
    assert "workflow:*:run" not in BUILT_IN_ROLES["operator"]
    assert "workflow:*:read" not in BUILT_IN_ROLES["operator"]


def test_collect_required_permissions_4_segment():
    from flux.security.auth_service import AuthService

    svc = AuthService.__new__(AuthService)
    svc._registry = None
    perms = svc._collect_required_permissions(
        namespace="billing",
        workflow_name="invoice",
        workflow_metadata={
            "task_names": ["load"],
            "nested_workflows": [["analytics", "summarize"]],
        },
        catalog=None,
    )
    assert "workflow:billing:invoice:run" in perms
    assert "workflow:billing:invoice:task:load:execute" in perms
    assert "workflow:analytics:summarize:run" in perms


def test_collect_required_permissions_visited_set_uses_tuple_key():
    """Two workflows with the same short name in different namespaces
    must both appear — a bare-string visited set would dedupe them.
    """
    from flux.security.auth_service import AuthService

    svc = AuthService.__new__(AuthService)
    svc._registry = None
    perms = svc._collect_required_permissions(
        namespace="billing",
        workflow_name="process",
        workflow_metadata={
            "task_names": [],
            "nested_workflows": [["analytics", "process"]],
        },
        _visited=set(),
        catalog=None,
    )
    # Both should appear — they're separate entities despite sharing a short name
    assert "workflow:billing:process:run" in perms
    assert "workflow:analytics:process:run" in perms


class TestBuiltInWorkerRole:
    def test_worker_role_exists(self):
        assert "worker" in BUILT_IN_ROLES

    def test_worker_role_permissions(self):
        perms = BUILT_IN_ROLES["worker"]
        assert "worker:*:*" in perms
        assert "config:*:read" in perms
        assert "admin:secrets:read" in perms
        assert "execution:*:read" in perms
        assert len(perms) == 4


class TestRevokeAllApiKeys:
    @pytest.mark.asyncio
    async def test_revoke_all_api_keys_deletes_all(self):
        config = AuthConfig()
        session = MagicMock()
        key1 = MagicMock(spec=APIKeyModel)
        key2 = MagicMock(spec=APIKeyModel)
        session.query.return_value.filter_by.return_value.all.return_value = [key1, key2]
        service = AuthService(config=config, session_factory=lambda: session)

        count = await service.revoke_all_api_keys("principal-1")

        assert count == 2
        session.delete.assert_any_call(key1)
        session.delete.assert_any_call(key2)
        session.commit.assert_called_once()
        session.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_revoke_all_api_keys_returns_zero_when_none(self):
        config = AuthConfig()
        session = MagicMock()
        session.query.return_value.filter_by.return_value.all.return_value = []
        service = AuthService(config=config, session_factory=lambda: session)

        count = await service.revoke_all_api_keys("principal-1")

        assert count == 0
        session.delete.assert_not_called()
        session.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_revoke_all_api_keys_rolls_back_on_error(self):
        config = AuthConfig()
        session = MagicMock()
        session.query.return_value.filter_by.return_value.all.side_effect = RuntimeError("db error")
        service = AuthService(config=config, session_factory=lambda: session)

        with pytest.raises(RuntimeError, match="db error"):
            await service.revoke_all_api_keys("principal-1")

        session.rollback.assert_called_once()
        session.close.assert_called_once()
