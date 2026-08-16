"""Add agents.hooks: agent-declared outbound hooks (declaration path 3).

Revision ID: 0028_agent_hooks
Revises: 0027_hooks
Create Date: 2026-08-16
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0028_agent_hooks"
down_revision: str | None = "0027_hooks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "agents"
_COLUMN = "hooks"


def upgrade() -> None:
    bind = op.get_bind()
    existing = {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}
    if _COLUMN not in existing:
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.JSON(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    existing = {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}
    if _COLUMN in existing:
        op.drop_column(_TABLE, _COLUMN)
