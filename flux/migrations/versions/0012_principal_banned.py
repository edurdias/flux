"""Add principals.banned: operator quarantine that registration respects.

``enabled`` alone cannot express quarantine: the reaper disables pruned
workers' principals and registration re-enables them when the worker
returns. ``banned`` is the explicit operator state — worker registration
refuses a banned principal instead of resurrecting it, and enabling a
banned principal is rejected until it is unbanned.

Revision ID: 0012_principal_banned
Revises: 0011_worker_join_tokens
Create Date: 2026-07-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_principal_banned"
down_revision: str | None = "0011_worker_join_tokens"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "principals"
_COLUMN = "banned"


def _column_names(bind) -> set[str]:
    return {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    if _COLUMN in _column_names(bind):
        return
    op.add_column(
        _TABLE,
        sa.Column(
            _COLUMN,
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    bind = op.get_bind()
    if _COLUMN in _column_names(bind):
        op.drop_column(_TABLE, _COLUMN)
