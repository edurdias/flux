"""Approval primitive — domain types and manager.

The ApprovalManager (full implementation arrives in Task 7) wraps the
ApprovalRequestModel repository. ApprovalRejected is the exception engine raises
when a human rejects a task call. ApprovalVerdict is what the engine reads
from a settled approval row.
"""

from __future__ import annotations

from dataclasses import dataclass


class ApprovalRejected(Exception):
    """Raised at the call site of a task whose approval gate was rejected.

    Workflow authors can ``try / except ApprovalRejected:`` to recover; the engine's
    retry, fallback, and rollback chains are deliberately skipped on this exception
    (see spec §2.6).
    """

    def __init__(
        self,
        *,
        task_name: str,
        approver_subject: str | None,
        approver_provider: str | None,
        reason: str | None,
    ):
        self.task_name = task_name
        self.approver_subject = approver_subject
        self.approver_provider = approver_provider
        self.reason = reason
        approver_repr = (
            f"{approver_subject}@{approver_provider}"
            if approver_subject
            else "<unknown>"
        )
        message = f"Approval rejected for task {task_name} by {approver_repr}"
        if reason:
            message += f": {reason}"
        super().__init__(message)


@dataclass
class ApprovalVerdict:
    """Outcome of waiting for an approval. Returned by ctx._await_approval()."""

    approved: bool
    approver_subject: str | None = None
    approver_provider: str | None = None
    reason: str | None = None
    cancelled: bool = False
