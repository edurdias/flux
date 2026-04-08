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

        engine = create_engine(f"sqlite:///{tmp_path}/test.db")
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        config = AuthConfig(api_keys=APIKeyAuthConfig(enabled=True))
        service = AuthService(config=config, session_factory=Session)
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
    async def test_role_deletion_blocked_by_sa(self, auth_service):
        await auth_service.create_role("temp", ["workflow:*:read"])
        await auth_service.create_service_account("svc-test", ["temp"])
        with pytest.raises(ValueError, match="referenced by service accounts"):
            await auth_service.delete_role("temp")

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
        await auth_service.create_service_account("svc-ci", ["operator"])
        key = await auth_service.create_api_key("svc-ci", "deploy-key")
        assert key.startswith("flux_sk_")
        keys = await auth_service.list_api_keys("svc-ci")
        assert len(keys) == 1
        await auth_service.revoke_api_key("svc-ci", "deploy-key")
        keys = await auth_service.list_api_keys("svc-ci")
        assert len(keys) == 0

    @pytest.mark.asyncio
    async def test_sa_lifecycle(self, auth_service):
        sa = await auth_service.create_service_account("svc-deploy", ["operator"])
        assert "operator" in sa.roles
        sa = await auth_service.update_service_account("svc-deploy", add_roles=["viewer"])
        assert "viewer" in sa.roles
        sa = await auth_service.update_service_account("svc-deploy", remove_roles=["operator"])
        assert "operator" not in sa.roles
        await auth_service.delete_service_account("svc-deploy")
        assert await auth_service.get_service_account("svc-deploy") is None

    @pytest.mark.asyncio
    async def test_authorize_preflight(self, auth_service):
        op = FluxIdentity(subject="alice@acme.com", roles=frozenset({"operator"}))
        result = await auth_service.authorize(
            op, "wf", {"task_names": ["load"], "nested_workflows": []}
        )
        assert result.ok is True

        await auth_service.create_role("limited", ["workflow:read-only:run"])
        limited = FluxIdentity(subject="charlie@acme.com", roles=frozenset({"limited"}))
        result = await auth_service.authorize(
            limited, "wf", {"task_names": ["load"], "nested_workflows": []}
        )
        assert result.ok is False
        assert "workflow:wf:run" in result.missing_permissions

    @pytest.mark.asyncio
    async def test_seed_idempotent(self, auth_service):
        auth_service.seed_built_in_roles()
        auth_service.seed_built_in_roles()
        roles = await auth_service.list_roles()
        assert sum(1 for r in roles if r.built_in) == 3


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
