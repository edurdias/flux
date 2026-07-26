"""Add schedules.overlap_policy plus skip accounting.

``overlap_policy`` decides what a due fire does while the schedule's previous
execution is still running. It is deliberately nullable with no server default:
existing rows stay NULL, which ``ScheduleModel.effective_overlap_policy`` reads
as "allow" -- exactly the behavior those schedules already had. Only schedules
created after this revision carry an explicit policy, so upgrading changes
nothing for anyone already running.

``skipped_count`` / ``last_skipped_at`` keep a skipped occurrence visible: it
produces no execution, and schedule history is derived from executions, so
without them a schedule quietly skipping every fire would look idle.

Revision ID: 0014_schedule_overlap_policy
Revises: 0013_worker_metadata
Create Date: 2026-07-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014_schedule_overlap_policy"
down_revision: str | None = "0013_worker_metadata"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "schedules"
_COLUMNS: tuple[tuple[str, sa.types.TypeEngine, dict], ...] = (
    ("overlap_policy", sa.String(), {"nullable": True}),
    ("skipped_count", sa.Integer(), {"nullable": False, "server_default": "0"}),
    ("last_skipped_at", sa.DateTime(), {"nullable": True}),
)


def upgrade() -> None:
    bind = op.get_bind()
    existing = {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}
    for name, column_type, kwargs in _COLUMNS:
        if name not in existing:
            op.add_column(_TABLE, sa.Column(name, column_type, **kwargs))


def downgrade() -> None:
    bind = op.get_bind()
    existing = {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}
    for name, _, _ in reversed(_COLUMNS):
        if name in existing:
            op.drop_column(_TABLE, name)
