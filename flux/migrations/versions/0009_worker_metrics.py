"""Add workers.metrics: latest advertised metrics snapshot.

Workers with a configured ``[flux.workers] metrics_provider`` attach a
validated ``dict[str, float]`` to their heartbeat pong; routing policies
read it through ``metric:*`` selectors and ``GET /workers`` surfaces it.
Additive and nullable — workers without a provider never write it.

Revision ID: 0009_worker_metrics
Revises: 0008_preferred_worker
Create Date: 2026-07-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_worker_metrics"
down_revision: str | None = "0008_preferred_worker"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "workers"
_COLUMN = "metrics"


def upgrade() -> None:
    bind = op.get_bind()
    existing = {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}
    if _COLUMN not in existing:
        # Matches Base64Type's storage: TEXT on PostgreSQL, VARCHAR elsewhere.
        column_type = sa.TEXT() if bind.dialect.name == "postgresql" else sa.String()
        op.add_column(_TABLE, sa.Column(_COLUMN, column_type, nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    existing = {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}
    if _COLUMN in existing:
        op.drop_column(_TABLE, _COLUMN)
