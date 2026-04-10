import pytest
from flux.security.identity import FluxIdentity
from flux.security.auth_service import AuthService
from flux.security.config import AuthConfig, APIKeyAuthConfig


class TestFullAuthorizationFlow:
    @pytest.fixture
    def auth_service(self, tmp_path):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from flux.models import Base
        from flux.security.principals import PrincipalRegistry

        engine = create_engine(f"sqlite:///{tmp_path}/test.db")
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        registry = PrincipalRegistry(session_factory=Session)
        config = AuthConfig(api_keys=APIKeyAuthConfig(enabled=True))
        service = AuthService(config=config, session_factory=Session, registry=registry)
        service.seed_built_in_roles()
        return service

    @pytest.mark.asyncio
    async def test_admin_can_do_anything(self, auth_service):
        identity = FluxIdentity(subject="admin@acme.com", roles=frozenset({"admin"}))
        assert await auth_service.is_authorized(identity, "workflow:any:run") is True
        assert await auth_service.is_authorized(identity, "admin:secrets:manage") is True

    @pytest.mark.asyncio
    async def test_operator_can_run_workflows(self, auth_service):
        identity = FluxIdentity(subject="alice@acme.com", roles=frozenset({"operator"}))
        assert await auth_service.is_authorized(identity, "workflow:report:run") is True
        assert (
            await auth_service.is_authorized(identity, "workflow:report:task:load:execute") is True
        )
        assert await auth_service.is_authorized(identity, "admin:secrets:manage") is False

    @pytest.mark.asyncio
    async def test_viewer_can_only_read(self, auth_service):
        identity = FluxIdentity(subject="bob@acme.com", roles=frozenset({"viewer"}))
        assert await auth_service.is_authorized(identity, "workflow:report:read") is True
        assert await auth_service.is_authorized(identity, "admin:secrets:manage") is False

    @pytest.mark.asyncio
    async def test_custom_role(self, auth_service):
        await auth_service.create_role("data-team", ["workflow:etl:*", "schedule:*:read"])
        identity = FluxIdentity(subject="charlie@acme.com", roles=frozenset({"data-team"}))
        assert await auth_service.is_authorized(identity, "workflow:etl:run") is True
        assert await auth_service.is_authorized(identity, "workflow:other:run") is False

    @pytest.mark.asyncio
    async def test_multiple_roles_merge(self, auth_service):
        await auth_service.create_role("scheduler", ["schedule:daily:manage"])
        identity = FluxIdentity(subject="dave@acme.com", roles=frozenset({"viewer", "scheduler"}))
        assert await auth_service.is_authorized(identity, "workflow:report:read") is True
        assert await auth_service.is_authorized(identity, "schedule:daily:manage") is True
        assert await auth_service.is_authorized(identity, "admin:secrets:write") is False

    @pytest.mark.asyncio
    async def test_role_deletion_not_blocked(self, auth_service):
        await auth_service.create_role("temp", ["workflow:*:read"])
        await auth_service.delete_role("temp")
        assert await auth_service.get_role("temp") is None

    @pytest.mark.asyncio
    async def test_builtin_role_immutable(self, auth_service):
        with pytest.raises(ValueError, match="Cannot modify"):
            await auth_service.update_role("admin", add_permissions=["extra"])
        with pytest.raises(ValueError, match="Cannot delete"):
            await auth_service.delete_role("admin")

    @pytest.mark.asyncio
    async def test_clone_role(self, auth_service):
        cloned = await auth_service.clone_role("operator", "restricted-op")
        assert cloned.name == "restricted-op"
        assert cloned.built_in is False
        original = await auth_service.get_role("operator")
        assert set(cloned.permissions) == set(original.permissions)

    @pytest.mark.asyncio
    async def test_api_key_lifecycle(self, auth_service):
        principal = await auth_service.create_principal(
            type="service_account",
            subject="svc-ci",
            external_issuer="flux",
            roles=["operator"],
        )
        key = await auth_service.create_api_key(principal.id, "deploy-key")
        assert key.startswith("flux_sk_")
        keys = await auth_service.list_api_keys(principal.id)
        assert len(keys) == 1
        await auth_service.revoke_api_key(principal.id, "deploy-key")
        keys = await auth_service.list_api_keys(principal.id)
        assert len(keys) == 0

    @pytest.mark.asyncio
    async def test_principal_lifecycle(self, auth_service):
        principal = await auth_service.create_principal(
            type="service_account",
            subject="svc-deploy",
            external_issuer="flux",
            roles=["operator"],
        )
        roles = auth_service._registry.get_roles(principal.id)
        assert "operator" in roles
        await auth_service.grant_role(principal.id, "viewer")
        roles = auth_service._registry.get_roles(principal.id)
        assert "viewer" in roles
        await auth_service.revoke_role(principal.id, "operator")
        roles = auth_service._registry.get_roles(principal.id)
        assert "operator" not in roles
        await auth_service.delete_principal(principal.id, force=False)
        assert await auth_service.get_principal(principal.id) is None

    @pytest.mark.asyncio
    async def test_authorize_preflight(self, auth_service):
        op = FluxIdentity(subject="alice@acme.com", roles=frozenset({"operator"}))
        result = await auth_service.authorize(
            op,
            "wf",
            {"task_names": ["load"], "nested_workflows": []},
        )
        assert result.ok is True

        await auth_service.create_role("limited", ["workflow:read-only:run"])
        limited = FluxIdentity(subject="charlie@acme.com", roles=frozenset({"limited"}))
        result = await auth_service.authorize(
            limited,
            "wf",
            {"task_names": ["load"], "nested_workflows": []},
        )
        assert result.ok is False
        assert "workflow:wf:run" in result.missing_permissions

    @pytest.mark.asyncio
    async def test_seed_idempotent(self, auth_service):
        auth_service.seed_built_in_roles()
        auth_service.seed_built_in_roles()
        roles = await auth_service.list_roles()
        assert sum(1 for r in roles if r.built_in) == 3

    @pytest.mark.asyncio
    async def test_authorize_nested_workflows(self, auth_service):
        """Test that authorize works with nested workflow metadata."""
        identity = FluxIdentity(subject="op@test", roles=frozenset({"operator"}))
        metadata = {
            "task_names": ["step_one"],
            "nested_workflows": [],
        }
        result = await auth_service.authorize(identity, "parent_wf", metadata)
        assert result.ok is True

    @pytest.mark.asyncio
    async def test_authorize_circular_workflows_no_crash(self, auth_service):
        """Verify circular workflow references don't cause infinite recursion."""
        identity = FluxIdentity(subject="op@test", roles=frozenset({"operator"}))
        metadata = {
            "task_names": ["step"],
            "nested_workflows": ["self_referencing"],
        }
        result = await auth_service.authorize(identity, "self_referencing", metadata)
        assert result.ok is True

    @pytest.mark.asyncio
    async def test_role_with_empty_permissions(self, auth_service):
        await auth_service.create_role("empty-role", [])
        identity = FluxIdentity(subject="test", roles=frozenset({"empty-role"}))
        assert await auth_service.is_authorized(identity, "anything") is False

    @pytest.mark.asyncio
    async def test_permission_validation_rejects_invalid(self, auth_service):
        with pytest.raises(ValueError, match="Invalid permission format"):
            await auth_service.create_role("bad-role", ["has spaces"])
        with pytest.raises(ValueError, match="Invalid permission format"):
            await auth_service.create_role("bad-role2", [""])


class TestEventSubjectIntegration:
    def test_identity_subject_on_events(self):
        from flux.domain.execution_context import ExecutionContext
        from flux.worker_registry import WorkerInfo

        ctx = ExecutionContext(workflow_id="wf-1", workflow_name="test")
        identity = FluxIdentity(subject="alice@acme.com", roles=frozenset({"operator"}))
        ctx.set_identity(identity)
        worker = WorkerInfo(name="worker-1")
        ctx.schedule(worker)
        event = [e for e in ctx.events if e.type.name == "WORKFLOW_SCHEDULED"][0]
        assert event.subject == "alice@acme.com"

    def test_events_without_identity(self):
        from flux.domain.execution_context import ExecutionContext
        from flux.worker_registry import WorkerInfo

        ctx = ExecutionContext(workflow_id="wf-1", workflow_name="test")
        worker = WorkerInfo(name="worker-1")
        ctx.schedule(worker)
        event = [e for e in ctx.events if e.type.name == "WORKFLOW_SCHEDULED"][0]
        assert event.subject is None
