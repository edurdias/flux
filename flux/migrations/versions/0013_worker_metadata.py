"""Add workers.worker_metadata: admin-written key/value metadata.

Server-held ``dict[str, str | float]`` written only through the
``/admin/workers/{name}/metadata`` routes — never by the worker — and
consumed by dispatch through ``meta(...)`` selectors in ``require(...)``
affinity expressions and ``score(...)`` routing policies. Additive and
nullable; NULL means no operator has attached metadata to the worker.

Revision ID: 0013_worker_metadata
Revises: 0012_principal_banned
Create Date: 2026-07-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_worker_metadata"
down_revision: str | None = "0012_principal_banned"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "workers"
_COLUMN = "worker_metadata"


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
