import hashlib
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from flux.security.models import RoleModel, APIKeyModel
from flux.security.principals import PrincipalModel
from flux.models import Base


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


class TestAPIKeyModel:
    def test_create_api_key(self):
        key_plaintext = "flux_sk_abc123def456"
        key_hash = hashlib.sha256(key_plaintext.encode()).hexdigest()
        key = APIKeyModel(
            principal_id="principal-id-123",
            name="ci-key",
            key_hash=key_hash,
            key_prefix=key_plaintext[:12],
        )
        assert key.name == "ci-key"
        assert key.key_prefix == "flux_sk_abc1"
        assert key.expires_at is None

    def test_api_key_with_expiry(self):
        key = APIKeyModel(
            principal_id="principal-id-123",
            name="temp-key",
            key_hash="hash",
            key_prefix="flux_sk_xxxx",
            expires_at=datetime.now() + timedelta(days=90),
        )
        assert key.expires_at is not None


class TestAPIKeyModelUniqueConstraints:
    @pytest.fixture
    def db_session(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            yield session

    def test_duplicate_key_name_per_service_account_raises(self, db_session):
        principal = PrincipalModel(
            type="service_account",
            subject="svc",
            external_issuer="flux",
            display_name="svc",
        )
        db_session.add(principal)
        db_session.flush()

        key1 = APIKeyModel(
            principal_id=principal.id,
            name="my-key",
            key_hash="hash1",
            key_prefix="flux_sk_aa",
        )
        db_session.add(key1)
        db_session.flush()

        key2 = APIKeyModel(
            principal_id=principal.id,
            name="my-key",
            key_hash="hash2",
            key_prefix="flux_sk_bb",
        )
        db_session.add(key2)
        with pytest.raises(IntegrityError):
            db_session.flush()

    def test_duplicate_key_hash_raises(self, db_session):
        principal = PrincipalModel(
            type="service_account",
            subject="svc2",
            external_issuer="flux",
            display_name="svc2",
        )
        db_session.add(principal)
        db_session.flush()

        key1 = APIKeyModel(
            principal_id=principal.id,
            name="key-a",
            key_hash="same_hash",
            key_prefix="flux_sk_aa",
        )
        db_session.add(key1)
        db_session.flush()

        key2 = APIKeyModel(
            principal_id=principal.id,
            name="key-b",
            key_hash="same_hash",
            key_prefix="flux_sk_bb",
        )
        db_session.add(key2)
        with pytest.raises(IntegrityError):
            db_session.flush()


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
