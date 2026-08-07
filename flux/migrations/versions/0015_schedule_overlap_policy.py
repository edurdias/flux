"""Add schedules.overlap_policy + skip statistics (issue #142).

overlap_policy is "skip" | "allow"; NULL — every row created before the
column existed — reads as "allow", the historic dispatch-regardless
behavior, so upgrades change nothing silently. skip_count /
last_skipped_at record overlap-skipped fires separately from run stats.

Revision ID: 0015_schedule_overlap_policy
Revises: 0014_task_cancelled_event
Create Date: 2026-08-06
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015_schedule_overlap_policy"
down_revision: str | None = "0014_task_cancelled_event"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "schedules"
_COLUMNS = (
    ("overlap_policy", sa.String()),
    ("skip_count", sa.Integer()),
    ("last_skipped_at", sa.DateTime()),
)


def upgrade() -> None:
    bind = op.get_bind()
    existing = {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}
    for name, type_ in _COLUMNS:
        if name not in existing:
            op.add_column(_TABLE, sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    existing = {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}
    for name, _ in _COLUMNS:
        if name in existing:
            op.drop_column(_TABLE, name)
