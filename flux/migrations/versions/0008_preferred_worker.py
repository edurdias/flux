"""Add executions.preferred_worker: sticky-routing hint for relayed calls.

A worker relaying a ``call()`` to the server tags the child execution with
its own name; dispatch prefers that worker when it is eligible (connected,
healthy, capacity, runner/label match), keeping mesh hops on the worker
whose module cache is already warm. Additive and nullable — a hint, never
a constraint.

Revision ID: 0008_preferred_worker
Revises: 0007_worker_runners
Create Date: 2026-07-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_preferred_worker"
down_revision: str | None = "0007_worker_runners"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "executions"
_COLUMN = "preferred_worker"


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
