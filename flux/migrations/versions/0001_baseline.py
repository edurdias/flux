"""Baseline schema.

Represents the schema as it existed when Alembic was introduced. On a fresh
database this creates every table and index from the current ORM metadata
(equivalent to the historical ``Base.metadata.create_all``). Pre-Alembic
databases are *stamped* at this revision by the runner rather than executing
it, since they already contain these tables.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-06-12
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from flux.models import Base

revision: str = "0001_baseline"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # checkfirst=True keeps this safe if a table somehow already exists.
    Base.metadata.create_all(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
