"""Event-driven execution dispatcher.

One ``Dispatcher`` task runs per server replica (``[flux.dispatch] mode =
"event"``). It replaces the legacy per-worker poll loops — which cost ~5 DB
queries per connected worker every 0.5s — with batch claims triggered by
wakeups: local work signals, PostgreSQL LISTEN/NOTIFY from other replicas,
and a slow safety-net tick.

Notifications are pure wakeups. The executions table remains the source of
truth, claimed with ``SELECT … FOR UPDATE SKIP LOCKED`` so concurrent
dispatchers on other replicas coordinate without locking each other out; a
missed NOTIFY is covered by the fallback tick.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from sqlalchemy import text

from flux.api.schemas import _inject_trace_context
from flux.config import Configuration
from flux.context_managers import ContextManager
from flux.utils import get_logger
from flux.utils import to_json

if TYPE_CHECKING:
    from flux.server import Server

logger = get_logger(__name__)

_NOTIFY_CHANNEL = "flux_work"
# Carries an execution_id payload when a checkpoint reaches a state a caller
# may be waiting on (finished/paused), so sync/stream waiters on OTHER
# replicas wake immediately instead of hitting their 30s poll fallback.
_EXEC_CHANNEL = "flux_exec"


@dataclass
class DispatchFrame:
    """An SSE frame queued for one worker, with enough metadata to release
    the underlying execution if the frame is never delivered."""

    kind: str  # execution_scheduled | execution_resumed | execution_cancelled
    execution_id: str
    frame: dict[str, Any]


class Dispatcher:
    def __init__(self, server: Server):
        self._server = server
        settings = Configuration.get().settings
        self._batch_size = settings.dispatch.batch_size
        self._fallback_interval = settings.dispatch.fallback_interval
        self._is_postgresql = settings.database_type == "postgresql"
        self._database_url = settings.database_url
        self._task: asyncio.Task | None = None
        self._listener_task: asyncio.Task | None = None

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="flux-dispatcher")
        if self._is_postgresql:
            self._listener_task = asyncio.create_task(
                self._listen_for_notifications(),
                name="flux-dispatch-listener",
            )
        logger.info(
            f"Event dispatcher started (batch_size={self._batch_size}, "
            f"fallback_interval={self._fallback_interval}s, "
            f"listen_notify={self._is_postgresql})",
        )

    async def stop(self) -> None:
        for task in (self._task, self._listener_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._task = None
        self._listener_task = None

    # -- wakeups -----------------------------------------------------------

    def notify_remote_replicas(self) -> None:
        """Fire-and-forget NOTIFY so dispatchers on other replicas wake.

        The local dispatcher is woken separately via the in-process event;
        this is purely the cross-replica signal and is skipped on SQLite
        (single-node by definition).
        """
        self._fire_notify(f"NOTIFY {_NOTIFY_CHANNEL}")

    def notify_execution_update(self, execution_id: str) -> None:
        """Wake sync/stream waiters for one execution on every replica.

        The local waiter is set directly by the checkpoint route; this relays
        the same signal cross-replica so a caller held open on replica A wakes
        as soon as the worker checkpoints through replica B, instead of
        waiting out the 30s poll fallback.
        """
        self._fire_notify(
            "SELECT pg_notify(:channel, :payload)",
            {"channel": _EXEC_CHANNEL, "payload": execution_id},
        )

    def _fire_notify(self, statement: str, params: dict | None = None) -> None:
        if not self._is_postgresql:
            return

        async def _send():
            try:
                manager = ContextManager.create()

                def _notify():
                    with manager.session() as session:
                        session.execute(text(statement), params or {})
                        session.commit()

                await asyncio.to_thread(_notify)
            except Exception as e:
                # Best-effort: the fallback tick / poll fallback covers it.
                logger.debug(f"NOTIFY failed ({statement}): {e}")

        asyncio.create_task(_send())

    async def _listen_for_notifications(self):
        """Hold a dedicated LISTEN connection; wake the dispatcher on NOTIFY.

        Best-effort with reconnect backoff — when the connection is down the
        fallback tick keeps dispatch moving.
        """
        import psycopg

        from flux.models import normalize_postgresql_url

        # psycopg accepts postgresql:// URIs; normalize legacy dialect tags
        # (postgresql+psycopg2://) first, then strip the driver marker.
        dsn = normalize_postgresql_url(self._database_url).replace(
            "postgresql+psycopg://",
            "postgresql://",
            1,
        )
        backoff = 1.0
        while True:
            try:
                conn = await psycopg.AsyncConnection.connect(dsn, autocommit=True)
                try:
                    await conn.execute(f"LISTEN {_NOTIFY_CHANNEL}")
                    await conn.execute(f"LISTEN {_EXEC_CHANNEL}")
                    backoff = 1.0
                    logger.debug(f"LISTEN {_NOTIFY_CHANNEL}/{_EXEC_CHANNEL} established")
                    async for notice in conn.notifies():
                        if notice.channel == _EXEC_CHANNEL:
                            waiter = self._server._execution_events.get(notice.payload)
                            if waiter:
                                waiter.set()
                        else:
                            self._server._work_available.set()
                finally:
                    await conn.close()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(
                    f"Dispatch LISTEN connection lost ({type(e).__name__}: {e}); "
                    f"reconnecting in {backoff:.0f}s",
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    # -- dispatch loop -----------------------------------------------------

    async def _run(self):
        while True:
            try:
                await asyncio.wait_for(
                    self._server._work_available.wait(),
                    timeout=self._fallback_interval,
                )
            except TimeoutError:
                pass
            except asyncio.CancelledError:
                raise
            self._server._work_available.clear()
            try:
                await self._dispatch_cycle()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.error("Dispatch cycle failed", exc_info=True)

    def _connected_workers(self):
        """Snapshot of dispatchable workers on this replica: a live SSE
        queue and not self-reported unhealthy (event-loop starvation)."""
        return [
            info
            for name, info in list(self._server._worker_info.items())
            if name in self._server._worker_queues and name not in self._server._worker_unhealthy
        ]

    async def _dispatch_cycle(self):
        workers = self._connected_workers()
        if not workers:
            return
        manager = ContextManager.create()

        # New executions: keep claiming while full batches come back.
        while True:
            assignments = await asyncio.to_thread(
                manager.next_executions_batch,
                workers,
                self._batch_size,
            )
            for ctx, worker_name in assignments:
                await self._deliver(manager, ctx, worker_name, "execution_scheduled")
            if len(assignments) < self._batch_size:
                break
            workers = self._connected_workers()
            if not workers:
                return

        resumes = await asyncio.to_thread(
            manager.next_resumes_batch,
            workers,
            self._batch_size,
        )
        for ctx, worker_name in resumes:
            await self._deliver(manager, ctx, worker_name, "execution_resumed")

        cancellations = await asyncio.to_thread(
            manager.next_cancellations_batch,
            [w.name for w in workers],
            self._batch_size,
        )
        for ctx in cancellations:
            queue = self._server._worker_queues.get(ctx.current_worker or "")
            if queue is None:
                continue
            queue.put_nowait(
                DispatchFrame(
                    kind="execution_cancelled",
                    execution_id=ctx.execution_id,
                    frame={
                        "id": f"{ctx.execution_id}_{uuid4().hex}",
                        "event": "execution_cancelled",
                        "data": _inject_trace_context(to_json({"context": ctx})),
                    },
                ),
            )

    async def _deliver(self, manager, ctx, worker_name: str, event: str):
        """Build the dispatch payload and enqueue it on the worker's SSE queue.

        If the worker's queue vanished (disconnect race) or the payload build
        fails, release the execution so another worker picks it up.
        """
        try:
            payload = await asyncio.to_thread(self._server._build_dispatch_payload, ctx)
            queue = self._server._worker_queues.get(worker_name)
            if queue is None:
                raise RuntimeError(f"worker {worker_name} disconnected before delivery")
            queue.put_nowait(
                DispatchFrame(
                    kind=event,
                    execution_id=ctx.execution_id,
                    frame={
                        "id": f"{ctx.execution_id}_{uuid4().hex}",
                        "event": event,
                        "data": _inject_trace_context(to_json(payload)),
                    },
                ),
            )
        except Exception as e:
            logger.warning(
                f"Failed to deliver {ctx.execution_id} to {worker_name} "
                f"({type(e).__name__}: {e}); releasing for redispatch",
            )
            try:
                await asyncio.to_thread(manager.unclaim, ctx.execution_id)
                self._server._work_available.set()
            except Exception:
                logger.error(
                    f"Failed to release execution {ctx.execution_id}",
                    exc_info=True,
                )
