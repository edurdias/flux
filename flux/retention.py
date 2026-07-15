"""Execution-history retention: bounded growth for the event-sourced store.

Without retention, ``executions`` and ``execution_events`` grow without bound
— every task of every execution is a persisted event row. When enabled
(``[flux.retention] enabled = true``), a background sweep deletes terminal
executions (COMPLETED / FAILED / CANCELLED) whose most recent event is older
than ``retention_days``, together with their dependent rows.

Cross-replica safety mirrors the scheduler: one sweep at a time fleet-wide via
a PostgreSQL advisory lock (SQLite is single-node, so the lock is a no-op).
Dependent rows are deleted explicitly rather than relying on FK cascades:
SQLite does not enforce them, and ``approval_requests`` has no cascade at all.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from collections.abc import Iterator

from sqlalchemy import func

from flux.config import Configuration
from flux.domain import ExecutionState
from flux.utils import get_logger

logger = get_logger(__name__)

# "FLUXR" — distinct from the scheduler's 0x464C555853 ("FLUXS").
_RETENTION_LOCK_KEY = 0x464C555852

_TERMINAL_STATES = (
    ExecutionState.COMPLETED,
    ExecutionState.FAILED,
    ExecutionState.CANCELLED,
)


class RetentionJob:
    def __init__(self):
        settings = Configuration.get().settings.retention
        self._retention_days = settings.retention_days
        self._sweep_interval = settings.sweep_interval
        self._batch_size = settings.batch_size
        self._task: asyncio.Task | None = None

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="flux-retention")
        logger.info(
            f"Retention job started (retention_days={self._retention_days}, "
            f"sweep_interval={self._sweep_interval}s)",
        )

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    # -- sweep loop --------------------------------------------------------

    async def _run(self):
        while True:
            try:
                deleted = await asyncio.to_thread(self._sweep)
                if deleted:
                    logger.info(f"Retention sweep deleted {deleted} execution(s)")
                # Dynamic-workflow GC rides the same cadence. No advisory
                # lock needed: the sweep is idempotent and bounded by
                # max_per_agent x agents, so a rare cross-replica overlap
                # only costs a redundant scan.
                dynamic_config = Configuration.get().settings.dynamic_workflows
                if dynamic_config.enabled and dynamic_config.ttl > 0:
                    from flux.dynamic_workflows import gc_sweep

                    await asyncio.to_thread(gc_sweep, ttl_seconds=dynamic_config.ttl)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.error("Retention sweep failed", exc_info=True)
            await asyncio.sleep(self._sweep_interval)

    def _sweep(self) -> int:
        """Delete expired terminal executions in batches; returns the count.

        Runs entirely in a worker thread. Each batch is one transaction, so a
        crash mid-sweep loses nothing and the next sweep resumes where this
        one stopped.
        """
        from flux.models import RepositoryFactory

        repository = RepositoryFactory.create_repository()
        with self._sweep_lock(repository._engine) as acquired:
            if not acquired:
                logger.debug("Another replica is sweeping; skipping this cycle")
                return 0

            cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
                days=self._retention_days,
            )
            total = 0
            while True:
                deleted = self._delete_batch(repository, cutoff)
                total += deleted
                if deleted < self._batch_size:
                    return total

    def _delete_batch(self, repository, cutoff: datetime) -> int:
        from flux.models import (
            AgentSessionModel,
            ApprovalRequestModel,
            ExecutionContextModel,
            ExecutionEventModel,
        )

        with repository.session() as session:
            last_event = (
                session.query(
                    ExecutionEventModel.execution_id.label("execution_id"),
                    func.max(ExecutionEventModel.time).label("last_time"),
                )
                .group_by(ExecutionEventModel.execution_id)
                .subquery()
            )
            rows = (
                session.query(ExecutionContextModel.execution_id)
                .join(
                    last_event,
                    last_event.c.execution_id == ExecutionContextModel.execution_id,
                )
                .filter(
                    ExecutionContextModel.state.in_(_TERMINAL_STATES),
                    last_event.c.last_time < cutoff,
                )
                .limit(self._batch_size)
                .all()
            )
            ids = [row[0] for row in rows]
            if not ids:
                return 0

            for model in (ExecutionEventModel, AgentSessionModel, ApprovalRequestModel):
                session.query(model).filter(model.execution_id.in_(ids)).delete(
                    synchronize_session=False,
                )
            session.query(ExecutionContextModel).filter(
                ExecutionContextModel.execution_id.in_(ids),
            ).delete(synchronize_session=False)
            session.commit()
            return len(ids)

    @staticmethod
    @contextmanager
    def _sweep_lock(engine) -> Iterator[bool]:
        """One retention sweep fleet-wide, via a PG session advisory lock.

        Same pattern as the scheduler's dispatch lock: held on a dedicated
        connection for the sweep's duration; if this replica dies mid-sweep,
        the connection drops and PostgreSQL releases the lock. SQLite is
        single-node and always acquires.
        """
        if engine.dialect.name != "postgresql":
            yield True
            return

        connection = engine.connect()
        try:
            acquired = bool(
                connection.exec_driver_sql(
                    "SELECT pg_try_advisory_lock(%(key)s)",
                    {"key": _RETENTION_LOCK_KEY},
                ).scalar(),
            )
            connection.commit()
            try:
                yield acquired
            finally:
                if acquired:
                    connection.exec_driver_sql(
                        "SELECT pg_advisory_unlock(%(key)s)",
                        {"key": _RETENTION_LOCK_KEY},
                    )
                    connection.commit()
        finally:
            connection.close()
