"""Add approval_requests.target_value: target-scoped standing grants (#143).

The resolved value of a task's declared approval_target argument, bound
at row creation. A decision with scope="target" grants "this task,
against this exact value, for this execution" — narrower than the
task-name-wide scope="execution" grant from #74. NULL — including every
pre-existing row — means no target was declared or bindable, so the row
can neither mint nor match a target-scoped grant (fail-closed).

Revision ID: 0018_approval_target_value
Revises: 0017_pause_wakes
Create Date: 2026-08-06
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018_approval_target_value"
down_revision: str | None = "0017_pause_wakes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "approval_requests"
_COLUMN = "target_value"


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
