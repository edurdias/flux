"""Add executions.park_deadline: bound on unclaimed parking (issue #157).

When set at submission (per-run park_ttl override or the
[flux.workers] park_ttl default), an execution still unclaimed past this
instant is failed terminally by the scheduler tick's sweep with a
ParkTimeoutError diagnostic. NULL — the default and every pre-existing
row — parks indefinitely, the historic behavior.

Revision ID: 0016_execution_park_deadline
Revises: 0015_schedule_overlap_policy
Create Date: 2026-08-06
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016_execution_park_deadline"
down_revision: str | None = "0015_schedule_overlap_policy"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "executions"
_COLUMN = "park_deadline"


def upgrade() -> None:
    bind = op.get_bind()
    existing = {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}
    if _COLUMN not in existing:
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.DateTime(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    existing = {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}
    if _COLUMN in existing:
        op.drop_column(_TABLE, _COLUMN)
