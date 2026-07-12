"""Add worker_join_tokens: one-time, short-lived worker registration tokens.

The shared bootstrap token is a fleet-wide master secret; join tokens are
the per-registration upgrade path (SEC3). Only the SHA-256 hash is stored;
used rows keep used_at/used_by as a join audit trail.

Revision ID: 0011_worker_join_tokens
Revises: 0010_approval_scope
Create Date: 2026-07-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_worker_join_tokens"
down_revision: str | None = "0010_approval_scope"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "worker_join_tokens"


def upgrade() -> None:
    bind = op.get_bind()
    if _TABLE in sa.inspect(bind).get_table_names():
        return
    op.create_table(
        _TABLE,
        sa.Column("id", sa.String(), primary_key=True, nullable=False),
        sa.Column("token_hash", sa.String(), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=True),
        sa.Column("used_by", sa.String(), nullable=True),
        sa.Column("created_by", sa.String(), nullable=True),
    )
    op.create_index("ix_worker_join_tokens_token_hash", _TABLE, ["token_hash"])


def downgrade() -> None:
    bind = op.get_bind()
    if _TABLE in sa.inspect(bind).get_table_names():
        op.drop_table(_TABLE)
