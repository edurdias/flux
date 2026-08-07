"""Convert execution_events.time to a timezone-aware UTC column.

Events are stamped on whichever machine creates them — the server schedules
and claims, the worker runs tasks and ships them in checkpoints — so a naive
timestamp left one execution's log carrying two clocks with no offset to
reconcile them. Events are now stamped ``datetime.now(timezone.utc)``; on
PostgreSQL a plain ``timestamp`` column would hand those back naive and every
comparison against a fresh stamp would raise.

Existing rows are pinned to UTC explicitly. Without the USING clause
PostgreSQL reinterprets naive values through the session ``TimeZone``, which
differs per deployment — the same rule ``ExecutionEvent`` applies in code
(naive means UTC) has to be spelled out here or the migration's result depends
on who runs it.

SQLite is skipped: it stores datetimes as text and ignores the column's
timezone flag entirely, so the rule is enforced on load instead.

Revision ID: 0020_event_time_utc
Revises: 0019_agent_approval_policy
Create Date: 2026-08-06
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020_event_time_utc"
down_revision: str | None = "0019_agent_approval_policy"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "execution_events"
_COLUMN = "time"


def _is_timezone_aware(bind) -> bool:
    column = next(
        (c for c in sa.inspect(bind).get_columns(_TABLE) if c["name"] == _COLUMN),
        None,
    )
    return bool(column and getattr(column["type"], "timezone", False))


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql" or _is_timezone_aware(bind):
        return
    op.alter_column(
        _TABLE,
        _COLUMN,
        type_=sa.DateTime(timezone=True),
        existing_type=sa.DateTime(),
        existing_nullable=False,
        postgresql_using=f"{_COLUMN} AT TIME ZONE 'UTC'",
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql" or not _is_timezone_aware(bind):
        return
    op.alter_column(
        _TABLE,
        _COLUMN,
        type_=sa.DateTime(),
        existing_type=sa.DateTime(timezone=True),
        existing_nullable=False,
        postgresql_using=f"{_COLUMN} AT TIME ZONE 'UTC'",
    )
