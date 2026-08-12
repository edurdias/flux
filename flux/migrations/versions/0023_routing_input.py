"""Add routing_input to executions and schedules.

Values the scheduler matches on that the target worker never receives (issue
#211). Kept off ``input`` rather than stripped out of it on the way to the
worker: the input is readable back through the execution API by anything
holding ``execution:*:read``, which the ``worker`` built-in role does, so
removing it at delivery would not hide it.

Signed like ``executions.input`` and ``schedules.input_data`` — caller-supplied
data deserialized in the dispatch loop, and dill executes on load.

Nullable with no backfill: NULL means no routing values, which is how every
execution before this revision behaves.

Revision ID: 0023_routing_input
Revises: 0022_execution_required_worker
Create Date: 2026-08-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023_routing_input"
down_revision: str | None = "0022_execution_required_worker"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMN = "routing_input"
_TABLES = ("executions", "schedules")


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for table in _TABLES:
        existing = {c["name"] for c in inspector.get_columns(table)}
        if _COLUMN not in existing:
            op.add_column(table, sa.Column(_COLUMN, sa.LargeBinary(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for table in _TABLES:
        existing = {c["name"] for c in inspector.get_columns(table)}
        if _COLUMN in existing:
            op.drop_column(table, _COLUMN)
