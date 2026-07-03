"""Index workers.last_seen_at for the heartbeat reaper sweep.

Every server replica's reaper runs ``find_stale`` each heartbeat interval
(default 10s), filtering workers on ``last_seen_at < threshold``. Without an
index that is a full scan of the workers table per sweep per replica, which
compounds at large fleet sizes.

Guarded like 0002: created only if absent, so databases that already picked
the index up from the ORM (fresh ``create_all`` schemas) upgrade cleanly.

Revision ID: 0004_worker_last_seen_index
Revises: 0003_worker_last_seen
Create Date: 2026-07-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_worker_last_seen_index"
down_revision: str | None = "0003_worker_last_seen"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "workers"
_INDEX = "ix_workers_last_seen_at"
_COLUMN = "last_seen_at"


def upgrade() -> None:
    bind = op.get_bind()
    present = {ix["name"] for ix in sa.inspect(bind).get_indexes(_TABLE)}
    if _INDEX not in present:
        op.create_index(_INDEX, _TABLE, [_COLUMN])


def downgrade() -> None:
    bind = op.get_bind()
    present = {ix["name"] for ix in sa.inspect(bind).get_indexes(_TABLE)}
    if _INDEX in present:
        op.drop_index(_INDEX, table_name=_TABLE)
