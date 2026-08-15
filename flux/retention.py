"""Execution-history retention: bounded growth for the event-sourced store.

Without retention, ``executions`` and ``execution_events`` grow without bound
— every task of every execution is a persisted event row. When enabled
(``[flux.retention] enabled = true``), a background sweep deletes terminal
executions (COMPLETED / FAILED / CANCELLED) whose most recent event is older
than ``retention_days``, together with their dependent rows, plus settled
hook deliveries (``delivered`` / ``dead``) past the same cutoff — an
outbound hook on a busy selector writes one row per matching event, so the
deliveries table grows on the same curve the executions table does.

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

# Hook deliveries the drain is finished with. `pending` is deliberately
# absent: see _delete_settled_deliveries.
_SETTLED_DELIVERY_STATUSES = ("delivered", "dead")


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
                    logger.info(f"Retention sweep deleted {deleted} row(s)")
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
        """Delete expired rows in batches; returns how many were deleted.

        Runs entirely in a worker thread. Each batch is one transaction, so a
        crash mid-sweep loses nothing and the next sweep resumes where this
        one stopped. A batch that deleted less than ``batch_size`` had
        nothing left to take, so the loop stops there.
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
            deleted = self._delete_settled_deliveries(session, cutoff)

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
            if ids:
                for model in (ExecutionEventModel, AgentSessionModel, ApprovalRequestModel):
                    session.query(model).filter(model.execution_id.in_(ids)).delete(
                        synchronize_session=False,
                    )
                session.query(ExecutionContextModel).filter(
                    ExecutionContextModel.execution_id.in_(ids),
                ).delete(synchronize_session=False)
                deleted += len(ids)

            if deleted:
                session.commit()
            return deleted

    def _delete_settled_deliveries(self, session, cutoff: datetime) -> int:
        """Prune expired hook deliveries that are done with, and only those.

        A ``pending`` row is an unmet obligation whatever its age — one
        waiting out a long backoff, or one an operator just handed back with
        ``hook retry`` — so it is never swept; deleting it would silently
        drop a delivery instead of dead-lettering it visibly.

        Age is read from ``created_at`` because it is the only stamp every
        settled row carries: a dead-lettered delivery never gets a
        ``delivered_at``.

        Deliveries are not dependents of the executions this batch deletes (a
        delivery's ``execution_id`` names the execution it *started*, not the
        one whose event it reports), so they are selected on their own — but
        under the same lock, cutoff and batch bound, in the same transaction.
        """
        from flux.models import HookDeliveryModel

        rows = (
            session.query(HookDeliveryModel.id)
            .filter(
                HookDeliveryModel.status.in_(_SETTLED_DELIVERY_STATUSES),
                HookDeliveryModel.created_at < cutoff,
            )
            .limit(self._batch_size)
            .all()
        )
        ids = [row[0] for row in rows]
        if not ids:
            return 0

        session.query(HookDeliveryModel).filter(HookDeliveryModel.id.in_(ids)).delete(
            synchronize_session=False,
        )
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
