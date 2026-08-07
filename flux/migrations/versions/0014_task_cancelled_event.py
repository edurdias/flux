"""Add TASK_CANCELLED to the executioneventtype enum (issue #149).

A task interrupted by cancellation now records an audit-only
TASK_CANCELLED event (and runs its declared rollback) instead of leaving
a bare TASK_STARTED behind. The event is invisible to the replay
short-circuit — replay still re-runs a task with no TASK_COMPLETED /
TASK_FAILED — so this value is additive and changes no stored rows.

Only PostgreSQL materializes sa.Enum as a native type; on SQLite the
column is a plain VARCHAR and nothing needs to change.

Revision ID: 0014_task_cancelled_event
Revises: 0013_worker_metadata
Create Date: 2026-08-05
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0014_task_cancelled_event"
down_revision: str | None = "0013_worker_metadata"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # ADD VALUE inside a transaction requires PostgreSQL 12+; the value
        # is not used within this migration, which is the remaining 12+
        # restriction.
        op.execute("ALTER TYPE executioneventtype ADD VALUE IF NOT EXISTS 'TASK_CANCELLED'")


def downgrade() -> None:
    # PostgreSQL cannot drop a value from an enum type in place, and any
    # persisted TASK_CANCELLED events would block a rewrite anyway. The value
    # is additive and harmless to older code paths, so downgrade keeps it.
    pass
