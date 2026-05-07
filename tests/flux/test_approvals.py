import os
import tempfile
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import select

from flux.models import ApprovalRequestModel, ApprovalStatus, RepositoryFactory


@pytest.fixture
def isolated_db():
    with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as f:
        db_path = f.name
    db_url = f"sqlite:///{db_path}"
    with patch("flux.config.Configuration.get") as mock_config:
        mock_config.return_value.settings.database_url = db_url
        mock_config.return_value.settings.database_type = "sqlite"
        mock_config.return_value.settings.security.auth.enabled = False
        yield
    if os.path.exists(db_path):
        os.unlink(db_path)


def test_approval_status_enum_values():
    assert ApprovalStatus.PENDING == "pending"
    assert ApprovalStatus.APPROVED == "approved"
    assert ApprovalStatus.REJECTED == "rejected"
    assert ApprovalStatus.CANCELLED == "cancelled"


def test_approval_request_model_persists_and_loads(isolated_db):
    repo = RepositoryFactory.create_repository()
    with repo.session() as s:
        row = ApprovalRequestModel(
            id="ap-test-1",
            execution_id="exec-1",
            task_call_id="call-1",
            workflow_namespace="default",
            workflow_name="release",
            task_name="deploy_to_prod",
            requested_at=datetime.now(timezone.utc),
            status=ApprovalStatus.PENDING,
        )
        s.add(row)
        s.commit()

        loaded = s.execute(
            select(ApprovalRequestModel).where(ApprovalRequestModel.id == "ap-test-1"),
        ).scalar_one()
        assert loaded.status == ApprovalStatus.PENDING
        assert loaded.workflow_namespace == "default"
        assert loaded.task_name == "deploy_to_prod"


def test_approval_request_unique_per_execution_and_call(isolated_db):
    repo = RepositoryFactory.create_repository()
    now = datetime.now(timezone.utc)
    with repo.session() as s:
        s.add(
            ApprovalRequestModel(
                id="ap-uniq-1",
                execution_id="exec-2",
                task_call_id="call-2",
                workflow_namespace="x",
                workflow_name="y",
                task_name="z",
                requested_at=now,
                status=ApprovalStatus.PENDING,
            ),
        )
        s.commit()
    with pytest.raises(Exception):
        with repo.session() as s:
            s.add(
                ApprovalRequestModel(
                    id="ap-uniq-2",
                    execution_id="exec-2",
                    task_call_id="call-2",
                    workflow_namespace="x",
                    workflow_name="y",
                    task_name="z",
                    requested_at=now,
                    status=ApprovalStatus.PENDING,
                ),
            )
            s.commit()


from flux.approvals import ApprovalRejected, ApprovalVerdict


def test_approval_rejected_carries_context():
    err = ApprovalRejected(
        task_name="default/release/deploy_to_prod",
        approver_subject="alice@example.com",
        approver_provider="oidc",
        reason="failed canary",
    )
    assert err.task_name == "default/release/deploy_to_prod"
    assert err.approver_subject == "alice@example.com"
    assert err.reason == "failed canary"
    assert "deploy_to_prod" in str(err)
    assert "alice@example.com" in str(err)


def test_approval_verdict_approved():
    v = ApprovalVerdict(
        approved=True,
        approver_subject="alice",
        approver_provider="oidc",
        reason=None,
    )
    assert v.approved is True
    assert v.cancelled is False


def test_approval_verdict_cancelled():
    v = ApprovalVerdict(approved=False, cancelled=True)
    assert v.approved is False
    assert v.cancelled is True
