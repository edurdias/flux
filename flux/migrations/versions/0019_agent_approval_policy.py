"""Add agents.autonomy / approval_routing: the approval_mode split (#146).

autonomy (strict/default/autonomous) is the ceiling — how much the agent
may do without asking; approval_routing (inline/notify) is where the
human is reached when a gate fires. NULL — every pre-#146 row — defers
to the legacy approval_mode mapping, so stored agents behave identically
after upgrade.

Revision ID: 0019_agent_approval_policy
Revises: 0018_approval_target_value
Create Date: 2026-08-06
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019_agent_approval_policy"
down_revision: str | None = "0018_approval_target_value"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "agents"
_COLUMNS = (
    ("autonomy", sa.String()),
    ("approval_routing", sa.String()),
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
