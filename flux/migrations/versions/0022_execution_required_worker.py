"""Add executions.required_worker: bind an execution to one worker.

``preferred_worker`` is a hint the scorer may ignore, which is the wrong
shape when the execution is a check *on* a worker (verification, an A/B
against one instance, reproducing a fault). Falling back to another worker
there is indistinguishable from success at the client, so the binding needs
its own column rather than a mode on the hint.

Nullable with no backfill: NULL means unbound, which is how every execution
before this revision behaves.

Revision ID: 0022_execution_required_worker
Revises: 0021_join_token_subject
Create Date: 2026-08-11
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022_execution_required_worker"
down_revision: str | None = "0021_join_token_subject"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "executions"
_COLUMN = "required_worker"


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
