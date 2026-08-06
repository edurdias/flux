"""Add executions.wake_at / wake_on_complete: pause wake conditions (#145).

A pause may now carry a wake condition — resume at an instant
(pause(until=/after=)) or when another execution reaches a terminal state
(pause(on_complete=)). The condition is stamped onto the execution row in
the same transaction as the PAUSED state write and cleared by every other
state write; the scheduler tick's wake pass reads the columns and fires
the resume. NULL — the default and every pre-existing row — means the
pause is indefinite, the historic behavior.

Revision ID: 0017_pause_wakes
Revises: 0016_execution_park_deadline
Create Date: 2026-08-06
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017_pause_wakes"
down_revision: str | None = "0016_execution_park_deadline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "executions"
_COLUMNS = (
    ("wake_at", sa.DateTime()),
    ("wake_on_complete", sa.String()),
)


def upgrade() -> None:
    bind = op.get_bind()
    existing = {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}
    for name, type_ in _COLUMNS:
        if name not in existing:
            op.add_column(_TABLE, sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    existing = {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}
    for name, _ in _COLUMNS:
        if name in existing:
            op.drop_column(_TABLE, name)
