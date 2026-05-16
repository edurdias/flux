"""Approval primitive — domain types and manager."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func as sa_func, select, update as sa_update

from flux.models import ApprovalRequestModel, ApprovalStatus, RepositoryFactory
from flux.unit_of_work import UnitOfWork


class ApprovalRejected(Exception):
    """Raised at the call site of a task whose approval gate was rejected.

    Workflow authors can ``try / except ApprovalRejected:`` to recover; the
    engine's retry, fallback, and rollback chains are deliberately skipped on
    this exception (see spec §2.6).
    """

    def __init__(
        self,
        task_name: str,
        approver_subject: str | None = None,
        approver_provider: str | None = None,
        reason: str | None = None,
    ):
        self.task_name = task_name
        self.approver_subject = approver_subject
        self.approver_provider = approver_provider
        self.reason = reason
        approver_repr = (
            f"{approver_subject}@{approver_provider}" if approver_subject else "<unknown>"
        )
        message = f"Approval rejected for task {task_name} by {approver_repr}"
        if reason:
            message += f": {reason}"
        super().__init__(message)

    def __reduce__(self):
        # Exceptions pickle as ``cls(*self.args)``; ``self.args`` is the
        # rendered message string, which does not round-trip through this
        # constructor. Reconstruct from the structured fields instead so the
        # exception survives the event-log pickle/unpickle cycle (the event
        # store uses dill). Positional args here require the constructor to
        # accept these four positionally — hence no keyword-only marker.
        return (
            self.__class__,
            (self.task_name, self.approver_subject, self.approver_provider, self.reason),
        )


class ApprovalAlreadyDecided(Exception):
    """Raised when a decide() is attempted on an approval row not in `pending` state.

    Surfaced as 409 Conflict by the server.
    """

    def __init__(self, current_status: ApprovalStatus, decided_at: datetime | None):
        self.current_status = current_status
        self.decided_at = decided_at
        super().__init__(
            f"Approval already decided ({current_status.value} at {decided_at})",
        )


@dataclass
class ApprovalVerdict:
    """Outcome of waiting for an approval. Returned by ctx._await_approval()."""

    approved: bool
    approver_subject: str | None = None
    approver_provider: str | None = None
    reason: str | None = None
    cancelled: bool = False


class ApprovalManager:
    """CRUD and decision logic for approval requests.

    All write methods take a ``uow`` so the caller controls the transaction
    boundary. Read methods accept an optional ``uow`` for read-your-writes
    consistency; if omitted they spin up a short-lived session.
    """

    def __init__(self) -> None:
        self._repository = RepositoryFactory.create_repository()

    def create(
        self,
        execution_id: str,
        task_call_id: str,
        workflow_namespace: str,
        workflow_name: str,
        task_name: str,
        *,
        uow: UnitOfWork,
    ) -> ApprovalRequestModel:
        """Insert a pending approval row. Caller commits the UoW."""
        row = ApprovalRequestModel(
            id=uuid.uuid4().hex,
            execution_id=execution_id,
            task_call_id=task_call_id,
            workflow_namespace=workflow_namespace,
            workflow_name=workflow_name,
            task_name=task_name,
            requested_at=datetime.now(timezone.utc),
            status=ApprovalStatus.PENDING,
        )
        uow.session.add(row)
        uow.session.flush()
        uow.session.expunge(row)
        return row

    def get(
        self,
        approval_id: str,
        *,
        uow: UnitOfWork | None = None,
    ) -> ApprovalRequestModel | None:
        if uow is not None:
            return uow.session.get(ApprovalRequestModel, approval_id)
        with self._repository.session() as s:
            return s.get(ApprovalRequestModel, approval_id)

    def get_by_call(
        self,
        execution_id: str,
        task_call_id: str,
        *,
        uow: UnitOfWork | None = None,
    ) -> ApprovalRequestModel | None:
        stmt = select(ApprovalRequestModel).where(
            ApprovalRequestModel.execution_id == execution_id,
            ApprovalRequestModel.task_call_id == task_call_id,
        )
        if uow is not None:
            return uow.session.execute(stmt).scalar_one_or_none()
        with self._repository.session() as s:
            return s.execute(stmt).scalar_one_or_none()

    @staticmethod
    def _filter_clauses(
        status: ApprovalStatus | None,
        execution_id: str | None,
        workflow_namespace: str | None,
        workflow_name: str | None,
        task_name: str | None,
        age_min: timedelta | None,
    ) -> list:
        clauses: list = []
        if status is not None:
            clauses.append(ApprovalRequestModel.status == status)
        if execution_id is not None:
            clauses.append(ApprovalRequestModel.execution_id == execution_id)
        if workflow_namespace is not None:
            clauses.append(ApprovalRequestModel.workflow_namespace == workflow_namespace)
        if workflow_name is not None:
            clauses.append(ApprovalRequestModel.workflow_name == workflow_name)
        if task_name is not None:
            clauses.append(ApprovalRequestModel.task_name == task_name)
        if age_min is not None:
            cutoff = datetime.now(timezone.utc) - age_min
            clauses.append(ApprovalRequestModel.requested_at <= cutoff)
        return clauses

    def list(
        self,
        *,
        status: ApprovalStatus | None = None,
        execution_id: str | None = None,
        workflow_namespace: str | None = None,
        workflow_name: str | None = None,
        task_name: str | None = None,
        age_min: timedelta | None = None,
        limit: int | None = 20,
        offset: int = 0,
        uow: UnitOfWork | None = None,
    ) -> Sequence[ApprovalRequestModel]:
        """List approvals matching the filters. ``limit=None`` returns every
        matching row (used when the caller must post-filter by authorization
        before paginating)."""
        clauses = self._filter_clauses(
            status,
            execution_id,
            workflow_namespace,
            workflow_name,
            task_name,
            age_min,
        )
        stmt = select(ApprovalRequestModel).where(*clauses)
        stmt = stmt.order_by(ApprovalRequestModel.requested_at.desc())
        if limit is not None:
            stmt = stmt.limit(limit)
        stmt = stmt.offset(offset)
        if uow is not None:
            return list(uow.session.execute(stmt).scalars())
        with self._repository.session() as s:
            return list(s.execute(stmt).scalars())

    def count(
        self,
        *,
        status: ApprovalStatus | None = None,
        execution_id: str | None = None,
        workflow_namespace: str | None = None,
        workflow_name: str | None = None,
        task_name: str | None = None,
        age_min: timedelta | None = None,
        uow: UnitOfWork | None = None,
    ) -> int:
        """Count approvals matching the filters, ignoring limit/offset."""
        clauses = self._filter_clauses(
            status,
            execution_id,
            workflow_namespace,
            workflow_name,
            task_name,
            age_min,
        )
        stmt = select(sa_func.count()).select_from(ApprovalRequestModel).where(*clauses)
        if uow is not None:
            return int(uow.session.execute(stmt).scalar_one())
        with self._repository.session() as s:
            return int(s.execute(stmt).scalar_one())

    def decide(
        self,
        execution_id: str,
        task_call_id: str,
        *,
        approver_subject: str,
        approver_provider: str,
        approved: bool,
        reason: str | None,
        uow: UnitOfWork,
    ) -> ApprovalRequestModel:
        """Record a decision via atomic CAS. Loser raises ApprovalAlreadyDecided."""
        new_status = ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED
        decided_at = datetime.now(timezone.utc)

        result = uow.session.execute(
            sa_update(ApprovalRequestModel)
            .where(
                ApprovalRequestModel.execution_id == execution_id,
                ApprovalRequestModel.task_call_id == task_call_id,
                ApprovalRequestModel.status == ApprovalStatus.PENDING,
            )
            .values(
                status=new_status,
                approver_subject=approver_subject,
                approver_provider=approver_provider,
                reason=reason,
                decided_at=decided_at,
            ),
        )

        if result.rowcount == 0:
            existing = uow.session.execute(
                select(ApprovalRequestModel).where(
                    ApprovalRequestModel.execution_id == execution_id,
                    ApprovalRequestModel.task_call_id == task_call_id,
                ),
            ).scalar_one_or_none()
            if existing is None:
                raise LookupError(
                    f"No approval found for execution={execution_id} task_call_id={task_call_id}",
                )
            raise ApprovalAlreadyDecided(existing.status, existing.decided_at)

        uow.session.flush()
        row = uow.session.execute(
            select(ApprovalRequestModel).where(
                ApprovalRequestModel.execution_id == execution_id,
                ApprovalRequestModel.task_call_id == task_call_id,
            ),
        ).scalar_one()
        uow.session.expunge(row)
        return row

    def cancel_pending_for_execution(
        self,
        execution_id: str,
        *,
        uow: UnitOfWork,
    ) -> int:
        """Mark all pending approvals for an execution as cancelled.

        Called by the cancellation handler in flux/server.py. Returns the
        count of rows updated.

        Uses a single conditional UPDATE gated on ``status == PENDING`` so a
        concurrent approve/reject committed between read and write is never
        overwritten — only rows still pending at write time are cancelled.
        """
        result = uow.session.execute(
            sa_update(ApprovalRequestModel)
            .where(
                ApprovalRequestModel.execution_id == execution_id,
                ApprovalRequestModel.status == ApprovalStatus.PENDING,
            )
            .values(
                status=ApprovalStatus.CANCELLED,
                decided_at=datetime.now(timezone.utc),
            ),
        )
        uow.session.flush()
        return int(result.rowcount)
