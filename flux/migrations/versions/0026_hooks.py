"""Add hooks and hook_deliveries: outbound event-driven workflow triggers.

A hook is a named subscription: when an engine event matches one of its
selectors, it starts workflow_ref as principal. A delivery is one hook's
obligation to react to one specific event -- written in the same transaction
as the event it reports (the outbox pattern) and drained later by the
scheduler tick, so no delivery blocks a checkpoint and no event is missed.

Revision ID: 0026_hooks
Revises: 0025_execution_name
Create Date: 2026-08-14
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026_hooks"
down_revision: str | None = "0025_execution_name"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_HOOKS_TABLE = "hooks"
_DELIVERIES_TABLE = "hook_deliveries"


def upgrade() -> None:
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())

    if _HOOKS_TABLE not in existing:
        op.create_table(
            _HOOKS_TABLE,
            sa.Column("id", sa.String(), primary_key=True, nullable=False),
            sa.Column("name", sa.String(), nullable=False, unique=True),
            sa.Column("enabled", sa.Boolean(), nullable=False),
            sa.Column("selectors", sa.JSON(), nullable=False),
            sa.Column("action", sa.String(), nullable=False),
            sa.Column("workflow_ref", sa.String(), nullable=False),
            # The principal's subject, as a schedule stores its SA's.
            sa.Column("principal", sa.String(), nullable=False),
            sa.Column("owner_type", sa.String(), nullable=False),
            sa.Column("owner_ref", sa.String(), nullable=False),
            sa.Column("max_attempts", sa.Integer(), nullable=False),
            sa.Column("created_by", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )

    if _DELIVERIES_TABLE not in existing:
        op.create_table(
            _DELIVERIES_TABLE,
            sa.Column("id", sa.String(), primary_key=True, nullable=False),
            sa.Column("hook_id", sa.String(), nullable=False),
            # "<execution_id>:<event_id>": the engine's event id repeats
            # across executions, so it is scoped before it keys anything.
            sa.Column("event_key", sa.String(), nullable=False),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column("attempts", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("execution_id", sa.String(), nullable=True),
            sa.Column("next_attempt_at", sa.DateTime(), nullable=True),
            sa.Column("last_error", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("delivered_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["hook_id"], ["hooks.id"], ondelete="CASCADE"),
            # The enqueue writes blind and lets the constraint dedupe: a
            # replayed or retried save cannot fan one event into two
            # deliveries.
            sa.UniqueConstraint("hook_id", "event_key", name="uq_hook_delivery_event"),
        )
        op.create_index(
            "ix_hook_deliveries_due",
            _DELIVERIES_TABLE,
            ["status", "next_attempt_at"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())
    if _DELIVERIES_TABLE in existing:
        op.drop_table(_DELIVERIES_TABLE)
    if _HOOKS_TABLE in existing:
        op.drop_table(_HOOKS_TABLE)
