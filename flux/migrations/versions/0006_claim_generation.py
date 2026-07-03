"""Add executions.claim_generation as a split-brain fencing token.

Bumped every time an execution is (re)assigned to a worker. Checkpoints carry
the generation they were claimed under; the server rejects a mismatch, so a
worker that was unclaimed while network-partitioned (but still running) cannot
interleave its writes with the new owner's.

Additive with a server default of 0, so existing rows and legacy workers
(which send no generation and are exempt from the check) are unaffected.

Revision ID: 0006_claim_generation
Revises: 0005_worker_capacity
Create Date: 2026-07-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_claim_generation"
down_revision: str | None = "0005_worker_capacity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "executions"
_COLUMN = "claim_generation"


def upgrade() -> None:
    bind = op.get_bind()
    existing = {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}
    if _COLUMN not in existing:
        op.add_column(
            _TABLE,
            sa.Column(_COLUMN, sa.Integer(), nullable=False, server_default="0"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    existing = {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}
    if _COLUMN in existing:
        op.drop_column(_TABLE, _COLUMN)
