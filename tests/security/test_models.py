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


class TestExecutionContextModelNewColumns:
    def test_model_has_exec_token_column(self):
        from flux.models import ExecutionContextModel
        from sqlalchemy import inspect as sa_inspect

        cols = {c.key for c in sa_inspect(ExecutionContextModel).mapper.column_attrs}
        assert "exec_token" in cols

    def test_model_has_scheduling_subject_column(self):
        from flux.models import ExecutionContextModel
        from sqlalchemy import inspect as sa_inspect

        cols = {c.key for c in sa_inspect(ExecutionContextModel).mapper.column_attrs}
        assert "scheduling_subject" in cols

    def test_model_has_scheduling_principal_issuer_column(self):
        from flux.models import ExecutionContextModel
        from sqlalchemy import inspect as sa_inspect

        cols = {c.key for c in sa_inspect(ExecutionContextModel).mapper.column_attrs}
        assert "scheduling_principal_issuer" in cols

    def test_model_init_accepts_new_fields(self):
        from flux.models import ExecutionContextModel

        m = ExecutionContextModel(
            execution_id="ex-1",
            workflow_id="wf-1",
            workflow_name="wf",
            input=None,
            exec_token="tok.abc",
            scheduling_subject="alice@acme.com",
            scheduling_principal_issuer="https://issuer",
        )
        assert m.exec_token == "tok.abc"
        assert m.scheduling_subject == "alice@acme.com"
        assert m.scheduling_principal_issuer == "https://issuer"


class TestExecutionContextModelRoundtrip:
    def test_from_plain_preserves_exec_token_on_model(self):
        from flux.models import ExecutionContextModel
        from flux.domain.execution_context import ExecutionContext
        from flux.domain.events import ExecutionState

        ctx = ExecutionContext(
            workflow_id="wf-1",
            workflow_namespace="default",
            workflow_name="wf",
            input=None,
            execution_id="ex-1",
            state=ExecutionState.SCHEDULED,
        )
        m = ExecutionContextModel.from_plain(ctx)
        assert m.exec_token is None

    def test_exec_token_stored_separately_from_context(self):
        from flux.models import ExecutionContextModel

        m = ExecutionContextModel(
            execution_id="ex-1",
            workflow_id="wf-1",
            workflow_name="wf",
            input=None,
            exec_token="tok.abc",
            scheduling_subject="alice@acme.com",
            scheduling_principal_issuer="https://issuer",
        )
        ctx = m.to_plain()
        data = ctx.to_dict()
        assert "exec_token" not in data, "exec_token must not appear in serialized context"
