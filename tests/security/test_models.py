import hashlib
from datetime import datetime, timedelta

from flux.security.models import RoleModel, ServiceAccountModel, APIKeyModel


class TestRoleModel:
    def test_create_role(self):
        role = RoleModel(
            name="data-engineer",
            permissions=["workflow:report:*", "schedule:*:read"],
            built_in=False,
        )
        assert role.name == "data-engineer"
        assert "workflow:report:*" in role.permissions
        assert role.built_in is False

    def test_create_builtin_role(self):
        role = RoleModel(
            name="admin",
            permissions=["*"],
            built_in=True,
        )
        assert role.built_in is True


class TestServiceAccountModel:
    def test_create_service_account(self):
        sa = ServiceAccountModel(
            name="svc-pipeline",
            roles=["operator"],
        )
        assert sa.name == "svc-pipeline"
        assert "operator" in sa.roles


class TestAPIKeyModel:
    def test_create_api_key(self):
        key_plaintext = "flux_sk_abc123def456"
        key_hash = hashlib.sha256(key_plaintext.encode()).hexdigest()
        key = APIKeyModel(
            service_account_id="sa-id-123",
            name="ci-key",
            key_hash=key_hash,
            key_prefix=key_plaintext[:12],
        )
        assert key.name == "ci-key"
        assert key.key_prefix == "flux_sk_abc1"
        assert key.expires_at is None

    def test_api_key_with_expiry(self):
        key = APIKeyModel(
            service_account_id="sa-id-123",
            name="temp-key",
            key_hash="hash",
            key_prefix="flux_sk_xxxx",
            expires_at=datetime.now() + timedelta(days=90),
        )
        assert key.expires_at is not None


class TestExecutionEventSubject:
    def test_event_with_subject(self):
        from flux.domain.events import ExecutionEvent, ExecutionEventType

        event = ExecutionEvent(
            type=ExecutionEventType.WORKFLOW_SCHEDULED,
            source_id="exec-123",
            name="test_workflow",
            subject="alice@acme.com",
        )
        assert event.subject == "alice@acme.com"

    def test_event_without_subject(self):
        from flux.domain.events import ExecutionEvent, ExecutionEventType

        event = ExecutionEvent(
            type=ExecutionEventType.WORKFLOW_STARTED,
            source_id="exec-123",
            name="test_workflow",
        )
        assert event.subject is None
