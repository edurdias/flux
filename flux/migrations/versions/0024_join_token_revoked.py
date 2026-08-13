"""Add worker_join_tokens.revoked_at so a token can be retired before its TTL.

Minting was additive with no inverse: a token left live by a failed bring-up
stayed claimable until it expired, and an operator could neither see what was
outstanding nor retire it (issue #197).

A soft delete rather than a row delete, so a revoked row keeps the audit trail
of who minted it, who retired it, and when. Note that nothing currently calls ``purge_expired``,
so no row is ever removed — revocation does not change that either way, but it
means the table only grows.

Nullable with no backfill: NULL means live, which is how every token before
this revision behaves.

Revision ID: 0024_join_token_revoked
Revises: 0023_routing_input
Create Date: 2026-08-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024_join_token_revoked"
down_revision: str | None = "0023_routing_input"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "worker_join_tokens"
_COLUMNS = {"revoked_at": sa.DateTime(), "revoked_by": sa.String()}


def upgrade() -> None:
    bind = op.get_bind()
    existing = {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}
    for name, type_ in _COLUMNS.items():
        if name not in existing:
            op.add_column(_TABLE, sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    existing = {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}
    for name in _COLUMNS:
        if name in existing:
            op.drop_column(_TABLE, name)
