"""Let worker GPU memory be NULL when the driver cannot report it.

``nvidia-smi`` answers ``[N/A]`` for every memory field on unified-memory
parts (GB10 / DGX Spark), and GPUtil parses that into ``nan``. The worker put
those straight into the registration payload, which then died at JSON
serialization and the worker never joined the mesh (issue #284).

The worker now sends ``None`` for an unreadable field, so the column has to
accept it. NULL means "the driver could not read this", which is a different
claim from zero: dispatch counts a GPU with unknown memory as available.

No backfill — every existing row carries a real number.

Revision ID: 0029_gpu_memory_nullable
Revises: 0028_agent_hooks
Create Date: 2026-09-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0029_gpu_memory_nullable"
down_revision: str | None = "0028_agent_hooks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "worker_resources_gpus"
_COLUMNS = ("memory_total", "memory_available")


def _nullable(bind) -> dict[str, bool]:
    return {c["name"]: bool(c["nullable"]) for c in sa.inspect(bind).get_columns(_TABLE)}


def _set_nullable(bind, nullable: bool) -> None:
    current = _nullable(bind)
    pending = [c for c in _COLUMNS if current.get(c) is not nullable and c in current]
    if not pending:
        return
    # SQLite cannot ALTER a column's nullability in place; batch mode rebuilds
    # the table. On PostgreSQL this emits a plain ALTER COLUMN.
    with op.batch_alter_table(_TABLE) as batch:
        for column in pending:
            batch.alter_column(
                column,
                existing_type=sa.BigInteger(),
                nullable=nullable,
            )


def upgrade() -> None:
    _set_nullable(op.get_bind(), True)


def downgrade() -> None:
    bind = op.get_bind()
    # A NOT NULL column cannot hold the rows this revision allowed in; drop
    # them rather than fail the downgrade on unreadable-memory workers.
    bind.execute(
        sa.text(
            f"DELETE FROM {_TABLE} WHERE memory_total IS NULL OR memory_available IS NULL",
        ),
    )
    _set_nullable(bind, False)
