"""Add workers.max_concurrent_executions for capacity-aware dispatch.

Workers advertise their capacity at registration; the dispatcher and the
poll-mode matching queries never assign more concurrent executions than a
worker's slot count. NULL (legacy workers that predate the field) and 0 both
mean unlimited, so the column is additive and needs no backfill.

Revision ID: 0005_worker_capacity
Revises: 0004_worker_last_seen_index
Create Date: 2026-07-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_worker_capacity"
down_revision: str | None = "0004_worker_last_seen_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "workers"
_COLUMN = "max_concurrent_executions"


def upgrade() -> None:
    bind = op.get_bind()
    existing = {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}
    if _COLUMN not in existing:
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.Integer(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    existing = {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}
    if _COLUMN in existing:
        op.drop_column(_TABLE, _COLUMN)
