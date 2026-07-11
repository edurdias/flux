"""Add approval_requests.scope: standing ("always") approval grants.

An approval decided with scope="execution" is a standing grant: later
approval gates on the same task name within that execution auto-approve
without pausing, each materializing an audit row. NULL (all pre-existing
rows) reads as "call" — the previous single-call semantics.

Revision ID: 0010_approval_scope
Revises: 0009_worker_metrics
Create Date: 2026-07-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_approval_scope"
down_revision: str | None = "0009_worker_metrics"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "approval_requests"
_COLUMN = "scope"


def upgrade() -> None:
    bind = op.get_bind()
    existing = {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}
    if _COLUMN not in existing:
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.String(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    existing = {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}
    if _COLUMN in existing:
        op.drop_column(_TABLE, _COLUMN)
