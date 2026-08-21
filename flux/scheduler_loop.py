"""The server-side scheduler tick.

Extracted from ``flux.server`` (#264 stage 2). One task per replica, taking
the cross-replica dispatch lock for a whole cycle so two replicas cannot
double-fire a schedule, and doing five jobs on each turn: firing due
schedules, sweeping the park TTL, resuming executions whose wake condition
came due, resolving orphaned cancellations, and draining outbound hooks.

**What it needs from the server, and why.** The dependencies arrive through
the constructor rather than through ``self``, so the coupling is countable:

- ``create_execution`` / ``session_factory`` -- starting a scheduled run and
  reading rows. The two capabilities a tick genuinely needs.
- ``execution_events`` / ``worker_queues`` -- shared dictionaries, not
  copies. The tick wakes an execution's waiters and hands resumed work to a
  worker's queue, so it writes into the same objects the request handlers
  read. That sharing is the honest remainder of this extraction: it is what
  a future engine-core boundary would have to turn into a channel.
- ``hook_starter`` / ``hook_authorizer`` -- the drain's two callables, bound
  by the server (see ``flux.hooks.dispatch``).

Lifecycle state (``task``, ``running``) belongs to this object now, rather
than to the server that starts it.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from collections.abc import Callable
from typing import Any

from flux.catalogs import WorkflowCatalog
from flux.config import Configuration
from flux.context_managers import ContextManager
from flux.execution_signals import ExecutionSignals
from flux.hooks.drain import drain_once
from flux.schedule_manager import create_schedule_manager
from flux.utils import get_logger

logger = get_logger(__name__)


class SchedulerLoop:
    """One replica's scheduler tick, with its own lifecycle."""

    def __init__(
        self,
        *,
        create_execution: Callable[..., Any],
        session_factory: Callable[[], Any],
        signals: ExecutionSignals,
        worker_queues: dict[str, asyncio.Queue],
        hook_starter: Callable[..., Any],
        hook_authorizer: Callable[..., Any],
        poll_interval: float,
    ) -> None:
        self._create_execution_fn = create_execution
        self._session_factory = session_factory
        self._signals = signals
        self._worker_queues = worker_queues
        self._hook_starter = hook_starter
        self._hook_authorizer = hook_authorizer
        self._poll_interval = poll_interval
        self.task: asyncio.Task | None = None
        self.running = False
        self._last_join_token_purge: float | None = None

    async def start(self) -> None:
        """Start the integrated scheduler background task"""
        if self.running:
            return

        self.running = True
        self.task = asyncio.create_task(self._loop())
        logger.info("Integrated scheduler started")

    async def stop(self) -> None:
        """Stop the integrated scheduler"""
        if not self.running:
            return

        self.running = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        logger.info("Integrated scheduler stopped")

    async def _loop(self) -> None:
        """Main scheduler loop - checks for due schedules periodically"""
        schedule_manager = create_schedule_manager()

        try:
            while self.running:
                try:
                    await asyncio.sleep(self._poll_interval)

                    # Only one replica dispatches per cycle. The lock spans the
                    # whole cycle — reading due schedules through the record_run
                    # that advances next_run_at — so replicas can't double-fire
                    # the same schedule. Skipped cycles cost a single try-lock.
                    with schedule_manager.dispatch_lock() as is_dispatcher:
                        if not is_dispatcher:
                            logger.debug(
                                "Another replica holds the scheduler dispatch lock; "
                                "skipping this cycle",
                            )
                            continue

                        # Get due schedules
                        current_time = datetime.now(timezone.utc)
                        due_schedules = schedule_manager.get_due_schedules(
                            current_time=current_time,
                        )

                        if due_schedules:
                            logger.info(f"Found {len(due_schedules)} due schedule(s)")

                        # Trigger each due schedule. Catch-up policy is
                        # run-once: record_run advances next_run_at from the
                        # current time, so a schedule due many intervals ago
                        # (server downtime) fires exactly once on recovery,
                        # not once per missed interval.
                        for schedule in due_schedules:
                            try:
                                # Overlap policy (issue #142): "skip" consumes
                                # this fire while a previous execution of the
                                # schedule is still non-terminal. NULL (rows
                                # from before the policy existed) means
                                # "allow" — dispatch regardless, the historic
                                # behavior.
                                if getattr(
                                    schedule,
                                    "overlap_policy",
                                    None,
                                ) == "skip" and schedule_manager.has_active_execution(schedule.id):
                                    logger.info(
                                        f"Schedule '{schedule.name}': previous execution "
                                        "still running; skipping this fire "
                                        "(overlap_policy=skip)",
                                    )
                                    schedule_manager.record_skip(schedule.id, current_time)
                                    continue
                                await self._trigger(schedule, current_time)
                            except Exception as e:
                                # The trigger path already recorded the failure before
                                # re-raising; recording here would double-count it.
                                logger.error(
                                    f"Failed to trigger schedule '{schedule.name}': {str(e)}",
                                    exc_info=True,
                                )

                        # Park-TTL sweep (issue #157): executions that opted
                        # into a bound and are still unclaimed past it fail
                        # terminally instead of waiting forever. Shares the
                        # dispatch lock so exactly one replica sweeps.
                        try:
                            expired = ContextManager.create().fail_expired_parked(current_time)
                            if expired:
                                logger.warning(
                                    f"Park TTL expired for {len(expired)} unclaimed "
                                    f"execution(s): {', '.join(expired)}",
                                )
                        except Exception:
                            logger.error("Park-TTL sweep failed", exc_info=True)

                        # Orphaned-cancellation sweep (issue #225):
                        # CANCELLING rows whose delivery target is gone —
                        # cancelled while parked (no worker to match) or
                        # assigned to a worker that never came back — resolve
                        # server-side instead of parking forever.
                        try:
                            workers_cfg = Configuration.get().settings.workers
                            orphaned = ContextManager.create().resolve_orphaned_cancellations(
                                list(self._worker_queues.keys()),
                                workers_cfg.cancellation_orphan_grace,
                                current_time,
                                # A worker is live if any replica heard from
                                # it within the window a heartbeat may
                                # legitimately lag before eviction.
                                liveness_seconds=(
                                    workers_cfg.heartbeat_timeout
                                    + workers_cfg.eviction_grace_period
                                ),
                            )
                            if orphaned:
                                logger.warning(
                                    f"Resolved {len(orphaned)} orphaned "
                                    f"cancellation(s): {', '.join(orphaned)}",
                                )
                                for execution_id in orphaned:
                                    event = self._signals.event(execution_id)
                                    if event:
                                        event.set()
                        except Exception:
                            logger.error(
                                "Orphaned-cancellation sweep failed",
                                exc_info=True,
                            )

                        # Pause-wake pass (issue #145): resume paused
                        # executions whose timed or completion wake fired.
                        # Same lock, same timing authority as the schedules.
                        try:
                            woken = ContextManager.create().fire_due_wakes(current_time)
                            if woken:
                                logger.info(
                                    f"Fired {len(woken)} pause wake(s): {', '.join(woken)}",
                                )
                        except Exception:
                            logger.error("Pause-wake pass failed", exc_info=True)

                        # Hook-delivery drain: the outbox recorded an
                        # obligation in the transaction that persisted the
                        # event; this is where it becomes an execution.
                        # Same lock as the sweeps above, so one replica
                        # fires each delivery, and batch-bounded so a
                        # backlog is drained over several ticks instead of
                        # holding the lock through one long pass.
                        try:
                            hooks_cfg = Configuration.get().settings.hooks
                            if hooks_cfg.enabled:
                                settled = await drain_once(
                                    self._hook_starter,
                                    now=current_time,
                                    batch_size=hooks_cfg.drain_batch_size,
                                    hop_limit=hooks_cfg.hop_limit,
                                    authorize=self._hook_authorizer,
                                )
                                if settled:
                                    logger.info(f"Settled {settled} hook delivery(ies)")
                        except Exception:
                            logger.error("Hook-delivery drain failed", exc_info=True)

                        # Join-token reaping (issue #197 follow-up): minting
                        # had no inverse, so dead rows accumulated forever.
                        # Same lock, so one replica reaps.
                        try:
                            self.purge_join_tokens()
                        except Exception:
                            logger.error("Join-token purge failed", exc_info=True)

                except Exception as e:
                    logger.error(f"Error in scheduler cycle: {str(e)}", exc_info=True)

        except asyncio.CancelledError:
            logger.info("Scheduler loop cancelled")

    async def _trigger(self, schedule, scheduled_time: datetime):
        """
        Trigger a scheduled workflow execution.
        Simple trigger-and-forget pattern - creates execution and lets workers handle it.
        """
        logger.info(
            f"Triggering scheduled workflow '{schedule.workflow_name}' (schedule: {schedule.name})",
        )

        schedule_manager = create_schedule_manager()
        try:
            from flux.security.auth_service import AuthService

            auth_config = Configuration.get().settings.security.auth
            identity = None
            sa_principal = None

            if auth_config.enabled:
                sa_name = getattr(schedule, "run_as_service_account", None)
                if not sa_name:
                    logger.error(
                        f"Schedule '{schedule.name}': no service account configured, skipping",
                    )
                    schedule_manager.record_failure(schedule.id)
                    return

                from flux.security.principals import PrincipalRegistry

                registry = PrincipalRegistry(session_factory=self._session_factory)
                db_auth_service = AuthService(
                    config=auth_config,
                    session_factory=self._session_factory,
                    registry=registry,
                )
                sa_principal = registry.find(sa_name, "flux")
                if sa_principal is None or not sa_principal.enabled:
                    logger.error(
                        f"Schedule '{schedule.name}': SA principal '{sa_name}' not found or disabled, skipping",
                    )
                    schedule_manager.record_failure(schedule.id)
                    return

                from flux.security.identity import FluxIdentity

                current_roles = registry.get_roles(sa_principal.id)
                identity = FluxIdentity(
                    subject=sa_principal.subject,
                    roles=frozenset(current_roles),
                    metadata={
                        "token_type": "service_account",
                        "issuer": "flux",
                        "via": "scheduler",
                    },
                )

                try:
                    _sched_ns = schedule.workflow_namespace
                    workflow = WorkflowCatalog.create().get(_sched_ns, schedule.workflow_name)
                    workflow_metadata = (
                        workflow.metadata or {} if hasattr(workflow, "metadata") else {}
                    )
                except Exception as e:
                    logger.error(
                        f"Schedule '{schedule.name}': workflow '{schedule.workflow_name}' not found: {e}",
                    )
                    schedule_manager.record_failure(schedule.id)
                    return

                auth_result = await db_auth_service.authorize(
                    identity,
                    _sched_ns,
                    schedule.workflow_name,
                    workflow_metadata,
                )
                if not auth_result.ok:
                    logger.error(
                        f"Schedule '{schedule.name}': SA '{sa_principal.subject}' lacks permissions: "
                        f"{auth_result.missing_permissions}",
                    )
                    schedule_manager.record_failure(schedule.id)
                    return

            _sched_ns = schedule.workflow_namespace
            ctx = self._create_execution_fn(
                _sched_ns,
                schedule.workflow_name,
                schedule.input_data,
                routing_input=schedule.routing_input,
            )

            if schedule.routing_input:
                # Key names only, as at the run endpoint. Schedules are where
                # routing values are set once and applied to every later fire,
                # so this is the trace that matters most.
                logger.info(
                    f"Execution {ctx.execution_id} carries routing input keys "
                    f"from schedule '{schedule.name}': {sorted(schedule.routing_input)}",
                )

            # Link the execution to its schedule (so history can be scoped to
            # this schedule) and, when auth is on, attach its execution token.
            # Both writes share one session/commit so the row is updated in a
            # single transaction rather than two independent ones.
            exec_token = None
            if auth_config.enabled and sa_principal is not None:
                from flux.security.execution_token import mint_execution_token

                exec_token = mint_execution_token(
                    subject=sa_principal.subject,
                    principal_issuer="flux",
                    execution_id=ctx.execution_id,
                    on_behalf_of=f"schedule:{schedule.name}",
                )

            sched_link_session = self._session_factory()
            try:
                from flux.models import ExecutionContextModel as _ECM_SCHED

                exec_row = sched_link_session.get(_ECM_SCHED, ctx.execution_id)
                if exec_row:
                    exec_row.schedule_id = schedule.id
                    if exec_token is not None and sa_principal is not None:
                        exec_row.exec_token = exec_token
                        exec_row.scheduling_subject = sa_principal.subject
                        exec_row.scheduling_principal_issuer = "flux"
                    sched_link_session.commit()
            finally:
                sched_link_session.close()

            # Persist the run: advances next_run_at and run stats in the DB so the
            # schedule is no longer due (mutating the detached object alone is lost).
            schedule_manager.record_run(schedule.id, scheduled_time)

            logger.info(
                f"Triggered execution '{ctx.execution_id}' for '{schedule.workflow_name}'",
            )

            from flux.observability import get_metrics

            m = get_metrics()
            if m:
                m.record_schedule_trigger(schedule.name, "success")

        except Exception as e:
            schedule_manager.record_failure(schedule.id)
            logger.error(f"Failed to trigger scheduled workflow: {str(e)}", exc_info=True)

            from flux.observability import get_metrics

            m = get_metrics()
            if m:
                m.record_schedule_trigger(schedule.name, "failure")

            raise

    def purge_join_tokens(self, *, now_monotonic: float | None = None) -> int:
        """Reap dead join-token rows, at most once an hour.

        Purging every tick would be wasted DELETEs against a table that
        changes on the cadence of fleet growth, so the tick calls this each
        cycle and the throttle makes it an hourly job. Rows are kept for
        ``[flux.workers] join_token_retention`` past expiry as an audit
        trail; 0 keeps them forever.
        """
        retention = Configuration.get().settings.workers.join_token_retention
        if retention <= 0:
            return 0
        now = time.monotonic() if now_monotonic is None else now_monotonic
        if self._last_join_token_purge is not None and now - self._last_join_token_purge < 3600:
            return 0

        from flux.security import join_tokens

        removed = join_tokens.purge_expired(older_than_seconds=retention)
        # Stamped only on success: a failed purge (the caller logs it) retries
        # on the next tick instead of being silenced for an hour.
        self._last_join_token_purge = now
        if removed:
            logger.info(f"Purged {removed} expired worker join token(s)")
        return removed
