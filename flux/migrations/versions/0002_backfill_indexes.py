"""Backfill hot-path indexes on pre-Alembic databases.

The historical ``create_all`` path never altered existing tables, so databases
created before the indexes were added (PR #79 onward) are missing them — and on
PostgreSQL that means sequential scans on the highest-volume tables. This
migration creates each expected index only if it is absent, so it is a no-op on
fresh databases (which already have them from the baseline) and repairs legacy
ones.

The index set is a static snapshot captured at authoring time; it is *not*
reflected from the live models, so this migration's behavior is fixed
regardless of future model changes.

Revision ID: 0002_backfill_indexes
Revises: 0001_baseline
Create Date: 2026-06-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_backfill_indexes"
down_revision: str | None = "0001_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (table, index_name, [columns]) — snapshot of the indexes the ORM defines.
_INDEXES: list[tuple[str, str, list[str]]] = [
    ("agent_sessions", "idx_agent_sessions_agent", ["agent_name"]),
    ("agent_sessions", "idx_agent_sessions_agent_started", ["agent_name", "started_at"]),
    ("approval_requests", "ix_approval_requests_execution_id", ["execution_id"]),
    ("approval_requests", "ix_approval_requests_requested_at", ["requested_at"]),
    ("approval_requests", "ix_approval_requests_task_name", ["task_name"]),
    ("approval_requests", "ix_approval_requests_workflow_name", ["workflow_name"]),
    ("approval_requests", "ix_approval_requests_workflow_namespace", ["workflow_namespace"]),
    ("approval_requests", "ix_approvals_status_requested", ["status", "requested_at"]),
    ("execution_events", "ix_execution_events_execution_id", ["execution_id"]),
    ("executions", "idx_execution_schedule_id", ["schedule_id"]),
    ("executions", "idx_execution_state", ["state"]),
    ("executions", "idx_execution_state_worker", ["state", "worker_name"]),
    ("executions", "idx_execution_worker_name", ["worker_name"]),
    ("executions", "ix_executions_workflow_id", ["workflow_id"]),
    ("executions", "ix_executions_workflow_name", ["workflow_name"]),
    ("executions", "ix_executions_workflow_namespace", ["workflow_namespace"]),
    ("schedules", "idx_schedule_next_run_at", ["next_run_at"]),
    ("schedules", "idx_schedule_status", ["status"]),
    ("schedules", "idx_schedule_status_next_run", ["status", "next_run_at"]),
    ("schedules", "idx_schedule_workflow_id", ["workflow_id"]),
    ("worker_packages", "ix_worker_packages_worker_name", ["worker_name"]),
    ("worker_resources", "ix_worker_resources_worker_name", ["worker_name"]),
    ("workflows", "ix_workflow_namespace_name", ["namespace", "name"]),
]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())
    for table, name, columns in _INDEXES:
        if table not in existing_tables:
            continue
        present = {ix["name"] for ix in inspector.get_indexes(table)}
        if name not in present:
            op.create_index(name, table, columns)


def downgrade() -> None:
    # Intentionally a no-op: upgrade() only creates indexes that are missing,
    # so on a fresh database these indexes belong to the baseline (0001), not to
    # this revision. Dropping them by name here would remove baseline,
    # performance-critical indexes from databases that always had them.
    pass
