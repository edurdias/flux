"""Add workers.last_seen_at for cross-replica heartbeat tracking.

Persists each worker's last heartbeat as a wall-clock timestamp so that any
server replica's reaper has a global view of worker liveness. Before this,
liveness lived only in the per-process ``_worker_last_pong`` map, so a worker
attached to one replica was invisible to the others and its executions could
not be reclaimed if that replica died.

Additive and nullable, so it is safe on both fresh and legacy databases and
needs no backfill (existing rows simply start NULL until the next heartbeat).

Revision ID: 0003_worker_last_seen
Revises: 0002_backfill_indexes
Create Date: 2026-06-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_worker_last_seen"
down_revision: str | None = "0002_backfill_indexes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "workers"
_COLUMN = "last_seen_at"


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
