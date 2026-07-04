"""Add workers.runners: runner capabilities advertised at registration.

Workflows declaring ``runner=...`` only dispatch to workers whose advertised
list contains it. Additive and nullable — NULL marks a legacy worker that
predates runners and executes everything in-process.

Revision ID: 0007_worker_runners
Revises: 0006_claim_generation
Create Date: 2026-07-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_worker_runners"
down_revision: str | None = "0006_claim_generation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "workers"
_COLUMN = "runners"


class _LegacyText(sa.types.TypeDecorator):
    """Frozen DDL snapshot of Base64Type: TEXT on PostgreSQL, VARCHAR on SQLite."""

    impl = sa.types.Text
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "sqlite":
            return dialect.type_descriptor(sa.types.String())
        return dialect.type_descriptor(sa.types.Text())


def upgrade() -> None:
    bind = op.get_bind()
    existing = {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}
    if _COLUMN not in existing:
        op.add_column(_TABLE, sa.Column(_COLUMN, _LegacyText(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    existing = {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}
    if _COLUMN in existing:
        op.drop_column(_TABLE, _COLUMN)
