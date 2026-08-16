"""Null out agents.approval_routing='notify' — the value was removed.

``notify`` was declared alongside ``inline`` (#146) for an out-of-band
approval delivery mechanism that #144 was meant to provide. #144 was
implemented, wired in, and reverted the same day pending a design rethink;
it never shipped, so ``notify`` sat as a validated field value with no
runtime behavior beyond a log line — a gate declared ``notify`` paused on
the current surface exactly like ``inline``. #144 closed as not-planned
and the value was removed from ``ROUTING_MODES`` rather than left as a
silent no-op.

Any stored row with the literal string ``notify`` would now fail
``AgentDefinition`` validation on every read (``manager.get()``/``list()``),
so this nulls it — behaviorally identical to what ``notify`` already did,
since NULL and ``inline`` both resolve to today's pause-and-wait behavior.

Revision ID: 0026_drop_notify_routing
Revises: 0025_execution_name
Create Date: 2026-08-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026_drop_notify_routing"
down_revision: str | None = "0025_execution_name"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "agents"


def upgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table(_TABLE):
        return
    agents = sa.table(_TABLE, sa.column("approval_routing", sa.String()))
    op.execute(
        agents.update().where(agents.c.approval_routing == "notify").values(approval_routing=None),
    )


def downgrade() -> None:
    # The specific rows that held 'notify' are not recoverable, and
    # rewriting them to 'notify' would resurrect a value the code no
    # longer accepts. NULL is behaviorally identical to the removed
    # value, so there is nothing to undo.
    pass
