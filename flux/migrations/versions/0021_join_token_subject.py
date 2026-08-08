"""Add worker_join_tokens.subject to bind a token to one worker name.

A join token recorded the name that consumed it but never checked it, so any
live token authorized registration under any name. Because registering an
existing name revokes the incumbent's API keys and issues a fresh one to the
caller, a holder of their own token could evict another worker and inherit the
admin-written metadata attached to its subject — the exact guarantee that
metadata is meant to provide.

The column is nullable and needs no backfill: NULL means unbound, which is how
every token minted before this revision behaves, so tokens already in flight
during an upgrade still work.

Revision ID: 0021_join_token_subject
Revises: 0020_event_time_utc
Create Date: 2026-08-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021_join_token_subject"
down_revision: str | None = "0020_event_time_utc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "worker_join_tokens"
_COLUMN = "subject"


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
