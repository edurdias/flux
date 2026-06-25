from __future__ import annotations

import asyncio
import time
from typing import Any
from collections.abc import AsyncIterator

import uvicorn
from fastapi import FastAPI
from fastapi import HTTPException
from fastapi import Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from flux import ExecutionContext
from flux.catalogs import WorkflowCatalog, WorkflowInfo
from flux.config import Configuration
from flux.workflow import workflow
from flux.context_managers import ContextManager
from flux.errors import WorkflowNotFoundError
from flux.utils import get_logger
from flux.servers.uvicorn_server import UvicornServer
from flux.servers.models import ExecutionContext as ExecutionContextDTO
from flux.utils import to_json
from flux.schedule_manager import create_schedule_manager
from flux.security.auth_service import AuthService
from flux.security.dependencies import init_auth_service
from flux.security.identity import FluxIdentity
from flux.api.auth_routes import AuthRoutesMixin
from flux.api.system_routes import SystemRoutesMixin
from flux.api.workflow_routes import WorkflowRoutesMixin
from flux.api.worker_routes import WorkerRoutesMixin
from flux.api.admin_routes import AdminRoutesMixin
from flux.api.schedule_routes import ScheduleRoutesMixin
from flux.api.execution_routes import ExecutionRoutesMixin
from flux.api.service_routes import ServiceRoutesMixin
from flux.api.rbac_routes import RbacRoutesMixin
from datetime import datetime, timedelta, timezone

# Re-exported for backward compatibility (these were defined here before the
# route modules were extracted). flux.api.schemas is the source of truth.
from flux.api.schemas import (  # noqa: F401
    MAX_WORKFLOW_UPLOAD_BYTES as MAX_WORKFLOW_UPLOAD_BYTES,
    SERVICE_NAME_RE as SERVICE_NAME_RE,
    _rate_limit_exceeded_handler as _rate_limit_exceeded_handler,
    _has_any_workflow_read as _has_any_workflow_read,
    _inject_trace_context as _inject_trace_context,
    WorkerRuntimeModel as WorkerRuntimeModel,
    WorkerGPUModel as WorkerGPUModel,
    WorkerResourcesModel as WorkerResourcesModel,
    WorkerRegistration as WorkerRegistration,
    SecretRequest as SecretRequest,
    SecretResponse as SecretResponse,
    ConfigRequest as ConfigRequest,
    ScheduleRequest as ScheduleRequest,
    ScheduleResponse as ScheduleResponse,
    ScheduleUpdateRequest as ScheduleUpdateRequest,
    RoleRequest as RoleRequest,
    RoleUpdateRequest as RoleUpdateRequest,
    RoleCloneRequest as RoleCloneRequest,
    ApprovalDecideRequest as ApprovalDecideRequest,
    APIKeyRequest as APIKeyRequest,
    TestTokenRequest as TestTokenRequest,
    PrincipalCreateRequest as PrincipalCreateRequest,
    PrincipalUpdateRequest as PrincipalUpdateRequest,
    RoleGrantRequest as RoleGrantRequest,
    PrincipalResponse as PrincipalResponse,
    WorkflowVersionResponse as WorkflowVersionResponse,
    ExecutionSummaryResponse as ExecutionSummaryResponse,
    ExecutionListResponse as ExecutionListResponse,
    WorkerResponse as WorkerResponse,
    HealthResponse as HealthResponse,
    AgentSessionSummaryResponse as AgentSessionSummaryResponse,
    AgentSessionListResponse as AgentSessionListResponse,
    ScheduleHistoryEntry as ScheduleHistoryEntry,
    ScheduleHistoryResponse as ScheduleHistoryResponse,
)

# Re-exported for backward compatibility: existing code/tests reference (and
# patch) these via the ``flux.server`` namespace even though the route
# handlers that use them now live in the ``flux.api`` route modules.
from flux.worker_registry import WorkerRegistry as WorkerRegistry  # noqa: F401, E402

logger = get_logger(__name__)


class Server(
    SystemRoutesMixin,
    WorkflowRoutesMixin,
    WorkerRoutesMixin,
    AdminRoutesMixin,
    ScheduleRoutesMixin,
    ExecutionRoutesMixin,
    ServiceRoutesMixin,
    RbacRoutesMixin,
    AuthRoutesMixin,
):
    """
    Server for managing workflows and tasks with integrated scheduler.
    """

    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port

        # Scheduler state
        self.scheduler_task = None
        self.scheduler_running = False

        self._work_available = asyncio.Event()
        self._worker_events: dict[str, asyncio.Event] = {}
        self._worker_names: list[str] = []
        self._worker_rr_index = 0
        self._execution_events: dict[str, asyncio.Event] = {}
        self._progress_buffers: dict[str, asyncio.Queue] = {}
        self._execution_queue_times: dict[str, float] = {}
        self._worker_last_pong: dict[str, float] = {}
        self._worker_cache: dict[str, WorkerResponse] = {}
        self._worker_offline_since: dict[str, float] = {}
        self._worker_evicted: dict[str, asyncio.Event] = {}
        self._worker_stale_since: dict[str, float] = {}
        # Resolved by the FastAPI lifespan startup hook in _create_api so that
        # merely constructing the app for tests / OpenAPI does not have the
        # side effect of generating the persisted token file.
        self._bootstrap_token: str | None = None
        self._worker_connection_gen: dict[str, int] = {}

        config = Configuration.get().settings.scheduling
        self.poll_interval = config.poll_interval

        workers_config = Configuration.get().settings.workers
        self.heartbeat_interval = workers_config.heartbeat_interval
        self.heartbeat_timeout = workers_config.heartbeat_timeout
        self.offline_ttl = workers_config.offline_ttl
        self.eviction_grace_period = workers_config.eviction_grace_period

        try:
            from flux.observability import setup as setup_observability

            obs_config = Configuration.get().settings.observability
            setup_observability(obs_config)
        except ImportError:
            logger.debug("Observability packages not installed, skipping setup")
        except Exception:
            logger.warning("Observability setup failed", exc_info=True)

    def _get_db_session(self):
        from flux.models import RepositoryFactory

        repo = RepositoryFactory.create_repository()
        return repo.session()

    def _notify_next_worker(self):
        """Signal the next connected worker in round-robin order."""
        if not self._worker_names:
            self._work_available.set()
            return

        # Try each worker once; if all are gone, fall back to broadcast
        for _ in range(len(self._worker_names)):
            idx = self._worker_rr_index % len(self._worker_names)
            self._worker_rr_index += 1
            name = self._worker_names[idx]
            event = self._worker_events.get(name)
            if event:
                event.set()
                return

        # Fallback: broadcast to all
        self._work_available.set()

    def start(self):
        """
        Start Flux server.
        """
        logger.info(f"Starting Flux server at {self.host}:{self.port}")
        logger.debug(f"Server version: {self._get_version()}")

        async def on_server_startup():
            logger.info("Flux server started successfully")
            logger.debug("Server is ready to accept connections")

            await self._start_scheduler()
            logger.info(f"Scheduler started (poll_interval={self.poll_interval}s)")

            self._reaper_task = asyncio.create_task(self._run_heartbeat_reaper())
            logger.info(
                f"Heartbeat reaper started (interval={self.heartbeat_interval}s, "
                f"timeout={self.heartbeat_timeout}s)",
            )

        try:
            config = uvicorn.Config(
                self._create_api(),
                host=self.host,
                port=self.port,
                log_level="warning",
                access_log=False,
            )
            server = UvicornServer(config, on_server_startup)
            server.run()
        except Exception as e:
            logger.error(f"Error starting Flux server: {str(e)}")
            raise
        finally:
            logger.info("Flux server stopped")
            logger.debug("Server shutdown complete")

    def _extract_token(self, authorization: str | None) -> str:
        if not authorization:
            raise HTTPException(status_code=401, detail="Authorization header missing")
        if not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Invalid authorization format")
        # Match flux.security.dependencies.get_identity: split exactly once so a
        # token containing spaces parses correctly, and strip surrounding
        # whitespace so a header like "Bearer  abc " yields "abc" rather than "".
        parts = authorization.split(" ", 1)
        token = parts[1].strip() if len(parts) == 2 else ""
        if not token:
            raise HTTPException(status_code=401, detail="Invalid authorization format")
        return token

    def _verify_worker_identity(self, identity: FluxIdentity, name: str) -> None:
        auth_config = Configuration.get().settings.security.auth
        if auth_config.enabled and identity.subject != name:
            from flux.observability import get_metrics as _gm_bind

            _m_bind = _gm_bind()
            if _m_bind:
                _m_bind.record_worker_auth_event(name, "identity_mismatch")
            raise HTTPException(
                status_code=403,
                detail=f"Worker identity mismatch: authenticated as '{identity.subject}', "
                f"but accessing endpoint for '{name}'",
            )

    def _get_version(self) -> str:
        import importlib.metadata

        try:
            version = importlib.metadata.version("flux-core")
        except importlib.metadata.PackageNotFoundError:
            version = "0.0.0"  # Default if package is not installed
        return version

    def _get_title(self) -> str:
        import importlib.metadata

        try:
            metadata = importlib.metadata.metadata("flux-core")
            # Use the description as title, or fall back to name
            title = metadata.get("Summary") or metadata.get("Name", "Flux")
            return f"{title} API"
        except importlib.metadata.PackageNotFoundError:
            return "Flux API"  # Default if package is not installed

    # ===========================================
    # Auto-Scheduling Helper
    # ===========================================

    def _auto_create_schedules_from_source(self, source: bytes, workflows: list[WorkflowInfo]):
        """Auto-create schedules for workflows by executing source and extracting schedule from workflow objects"""
        config = Configuration.get().settings.scheduling

        if not config.auto_schedule_enabled:
            logger.debug("Auto-scheduling disabled in configuration")
            return

        try:
            module_globals: dict[str, Any] = {}
            exec(source, module_globals)

            schedule_manager = create_schedule_manager()

            for workflow_info in workflows:
                workflow_obj = None

                for obj in module_globals.values():
                    if (
                        isinstance(obj, workflow)
                        and obj.namespace == workflow_info.namespace
                        and obj.name == workflow_info.name
                    ):
                        workflow_obj = obj
                        break

                if workflow_obj is None or workflow_obj.schedule is None:
                    continue

                schedule_name = f"{workflow_info.name}{config.auto_schedule_suffix}"

                try:
                    existing_schedules = schedule_manager.list_schedules(
                        workflow_id=workflow_info.id,
                        active_only=False,
                    )
                    existing = next(
                        (s for s in existing_schedules if s.name == schedule_name),
                        None,
                    )

                    if existing:
                        schedule_manager.update_schedule(
                            schedule_id=existing.id,
                            schedule=workflow_obj.schedule,
                            description="Auto-created from workflow decorator",
                        )
                        logger.info(
                            f"Updated auto-schedule '{schedule_name}' for workflow '{workflow_info.name}'",
                        )
                    else:
                        schedule_manager.create_schedule(
                            workflow_id=workflow_info.id,
                            workflow_namespace=workflow_info.namespace,
                            workflow_name=workflow_info.name,
                            name=schedule_name,
                            schedule=workflow_obj.schedule,
                            description="Auto-created from workflow decorator",
                            input_data=None,
                        )
                        logger.info(
                            f"Created auto-schedule '{schedule_name}' for workflow '{workflow_info.name}'",
                        )

                except Exception as e:
                    logger.error(
                        f"Failed to auto-create schedule for workflow '{workflow_info.name}': {str(e)}",
                        exc_info=True,
                    )

        except Exception as e:
            logger.error(
                f"Failed to execute workflow source for schedule extraction: {str(e)}",
                exc_info=True,
            )

    # ===========================================
    # Internal Execution Helper
    # ===========================================

    def _create_execution(
        self,
        namespace: str,
        workflow_name: str,
        input_data: Any = None,
        version: int | None = None,
    ) -> ExecutionContext:
        workflow = WorkflowCatalog.create().get(namespace, workflow_name, version)
        if not workflow:
            raise WorkflowNotFoundError(f"Workflow '{namespace}/{workflow_name}' not found")

        ctx = ContextManager.create().save(
            ExecutionContext(
                workflow_id=workflow.id,
                workflow_namespace=workflow.namespace,
                workflow_name=workflow.name,
                input=input_data,
                requests=workflow.requests,
            ),
        )

        self._execution_queue_times[ctx.execution_id] = time.monotonic()

        from flux.observability import get_metrics

        m = get_metrics()
        if m:
            m.record_workflow_started(ctx.workflow_namespace, ctx.workflow_name)
            m.record_execution_queued()

        return ctx

    async def _stream_execution_events(
        self,
        ctx: ExecutionContext,
        manager: ContextManager,
        detailed: bool,
        emit_initial: bool = False,
    ) -> AsyncIterator[dict]:
        event = self._execution_events[ctx.execution_id]
        progress_buffer = self._progress_buffers.get(ctx.execution_id)
        active_tasks: set[asyncio.Task] = set()
        if emit_initial:
            # Emit the current state immediately so a consumer attaching after
            # the execution already finished still receives the terminal
            # frame — the loop below exits at once when ctx.has_finished.
            dto = ExecutionContextDTO.from_domain(ctx)
            yield {
                "event": f"{ctx.workflow_name}.execution.{ctx.state.value.lower()}",
                "data": to_json(dto if detailed else dto.summary()),
            }
        try:
            while not ctx.has_finished:
                if progress_buffer:
                    progress_task = asyncio.create_task(progress_buffer.get())
                    checkpoint_task = asyncio.create_task(event.wait())
                    active_tasks = {progress_task, checkpoint_task}

                    done, pending = await asyncio.wait(
                        active_tasks,
                        timeout=30.0,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for t in pending:
                        t.cancel()
                    if pending:
                        await asyncio.gather(*pending, return_exceptions=True)
                    active_tasks.clear()

                    if not done:
                        continue

                    if progress_task in done:
                        item = progress_task.result()
                        items = [item]
                        while not progress_buffer.empty():
                            items.append(progress_buffer.get_nowait())
                        for p in items:
                            yield {
                                "event": "task.progress",
                                "data": to_json(
                                    {
                                        "type": p.type.value,
                                        "source_id": p.source_id,
                                        "name": p.name,
                                        "value": p.value,
                                        "time": str(p.time),
                                    },
                                ),
                            }

                    if checkpoint_task in done or event.is_set():
                        event.clear()
                        new_ctx = manager.get(ctx.execution_id)
                        if new_ctx.events and (
                            not ctx.events or new_ctx.events[-1].time > ctx.events[-1].time
                        ):
                            ctx = new_ctx
                            dto = ExecutionContextDTO.from_domain(ctx)
                            yield {
                                "event": f"{ctx.workflow_name}.execution.{ctx.state.value.lower()}",
                                "data": to_json(dto if detailed else dto.summary()),
                            }
                else:
                    try:
                        await asyncio.wait_for(event.wait(), timeout=30.0)
                    except TimeoutError:
                        pass
                    event.clear()
                    new_ctx = manager.get(ctx.execution_id)
                    if new_ctx.events and (
                        not ctx.events or new_ctx.events[-1].time > ctx.events[-1].time
                    ):
                        ctx = new_ctx
                        dto = ExecutionContextDTO.from_domain(ctx)
                        yield {
                            "event": f"{ctx.workflow_name}.execution.{ctx.state.value.lower()}",
                            "data": to_json(dto if detailed else dto.summary()),
                        }
        finally:
            for t in active_tasks:
                if not t.done():
                    t.cancel()
            if active_tasks:
                await asyncio.gather(*active_tasks, return_exceptions=True)
            self._execution_events.pop(ctx.execution_id, None)
            self._progress_buffers.pop(ctx.execution_id, None)
            try:
                from flux.domain import ExecutionState as _ExecutionState

                latest = manager.get(ctx.execution_id)
                if latest and latest.state == _ExecutionState.RESUME_SCHEDULED:
                    manager.unclaim(ctx.execution_id)
                    logger.info(
                        f"SSE disconnect: reverted {ctx.execution_id} from "
                        f"RESUME_SCHEDULED back to RESUMING",
                    )
            except Exception as exc:
                logger.warning(
                    f"Failed to revert RESUME_SCHEDULED on SSE disconnect for "
                    f"{ctx.execution_id}: {exc}",
                )

    # ===========================================
    # Integrated Scheduler Methods
    # ===========================================

    async def _start_scheduler(self):
        """Start the integrated scheduler background task"""
        if self.scheduler_running:
            return

        self.scheduler_running = True
        self.scheduler_task = asyncio.create_task(self._scheduler_loop())
        logger.info("Integrated scheduler started")

    async def _stop_scheduler(self):
        """Stop the integrated scheduler"""
        if not self.scheduler_running:
            return

        self.scheduler_running = False
        if self.scheduler_task:
            self.scheduler_task.cancel()
            try:
                await self.scheduler_task
            except asyncio.CancelledError:
                pass
        logger.info("Integrated scheduler stopped")

    async def _stop_reaper(self):
        """Stop the heartbeat reaper task."""
        if hasattr(self, "_reaper_task") and self._reaper_task:
            self._reaper_task.cancel()
            try:
                await self._reaper_task
            except asyncio.CancelledError:
                pass
            logger.info("Heartbeat reaper stopped")

    def _persist_worker_heartbeat(self, name: str) -> None:
        from flux.worker_registry import WorkerRegistry

        WorkerRegistry.create().record_heartbeat(name)

    async def _record_heartbeat(self, name: str) -> None:
        """Record a worker heartbeat: in-memory fast-path + persisted timestamp.

        The in-memory ``_worker_last_pong`` drives this replica's round-robin
        and local stale tracking; the persisted ``last_seen_at`` gives every
        replica's reaper a global view of liveness so orphaned executions can
        be reclaimed when the replica a worker was attached to dies. Persisting
        runs off the event loop and never fails the request.
        """
        self._worker_last_pong[name] = time.monotonic()
        self._worker_stale_since.pop(name, None)
        try:
            await asyncio.to_thread(self._persist_worker_heartbeat, name)
        except Exception as e:
            logger.debug(f"Failed to persist heartbeat for worker {name}: {e}")

    def _disconnect_worker(self, name: str, reason: str = "disconnect") -> None:
        """Remove a worker from the connected set and mark it offline in cache."""
        self._worker_events.pop(name, None)
        self._worker_last_pong.pop(name, None)
        if name in self._worker_names:
            self._worker_names.remove(name)
        self._worker_offline_since[name] = time.monotonic()
        if name in self._worker_cache:
            self._worker_cache[name].status = "offline"
        evicted = self._worker_evicted.pop(name, None)
        if evicted:
            evicted.set()

        from flux.observability import get_metrics

        m = get_metrics()
        if m:
            m.record_worker_disconnected(name, reason)

        if reason == "evicted":
            from flux.security.dependencies import _get_auth_service
            from flux.security.principals import PrincipalRegistry

            _auth_svc = _get_auth_service()
            if _auth_svc is not None:

                async def _revoke_worker_key():
                    try:
                        registry = PrincipalRegistry(session_factory=self._get_db_session)
                        principal = registry.find(subject=name, external_issuer="flux")
                        if principal:
                            await _auth_svc.revoke_all_api_keys(principal.id)
                            logger.info(f"Revoked API key for evicted worker {name}")

                            from flux.observability import get_metrics as _gm_evict

                            _m_evict = _gm_evict()
                            if _m_evict:
                                _m_evict.record_worker_auth_event(
                                    name,
                                    "key_revoked",
                                )
                    except Exception as e:
                        logger.warning(f"Failed to revoke API key for worker {name}: {e}")

                try:
                    asyncio.create_task(_revoke_worker_key())
                except RuntimeError:
                    logger.warning(f"Cannot revoke API key for {name}: no event loop")

    def _unclaim_worker_executions(self, worker_name: str) -> None:
        """Recover all executions assigned to an evicted worker.

        Queries the DB directly instead of relying on in-memory tracking,
        so dispatched-but-not-yet-claimed executions are also recovered.
        """
        context_manager = ContextManager.create()
        executions = context_manager.find_by_worker(worker_name)
        if not executions:
            return

        from flux.domain import ExecutionState
        from flux.observability import get_metrics

        for ctx in executions:
            try:
                unclaimed = context_manager.unclaim(ctx.execution_id)
                if unclaimed.state in (ExecutionState.PAUSED, ExecutionState.RESUMING):
                    context_manager.release_worker(ctx.execution_id)
                self._execution_queue_times[ctx.execution_id] = time.monotonic()
                m = get_metrics()
                if m:
                    m.record_execution_queued()
                    if ctx.state in (
                        ExecutionState.RESUMING,
                        ExecutionState.RESUME_SCHEDULED,
                        ExecutionState.RESUME_CLAIMED,
                    ):
                        m.record_resume_queued(
                            ctx.workflow_namespace,
                            ctx.workflow_name,
                        )
                logger.info(
                    f"Unclaimed execution {ctx.execution_id} from evicted worker {worker_name}",
                )
                event = self._execution_events.get(ctx.execution_id)
                if event:
                    event.set()
            except Exception as e:
                logger.error(f"Failed to unclaim execution {ctx.execution_id}: {e}")

        self._work_available.set()

    async def _reclaim_orphaned_executions(self) -> None:
        """Reclaim executions stranded by a dead replica (cross-replica sweep).

        The local stale/evict path above only sees workers attached to *this*
        replica. If the replica a worker was attached to dies, no local reaper
        knows the worker is gone. This sweep reads the persisted ``last_seen_at``
        so any surviving replica can detect a globally-stale worker — one no
        replica has heard from for the full stale-plus-grace window — and
        reclaim its executions.

        Workers connected to this replica are skipped (the local path owns them).
        ``unclaim`` converges idempotently, so it is safe if several replicas
        run this sweep concurrently for the same orphan.
        """
        deadline_seconds = self.heartbeat_timeout + self.eviction_grace_period
        threshold = datetime.now(timezone.utc) - timedelta(seconds=deadline_seconds)
        try:
            from flux.worker_registry import WorkerRegistry

            registry = WorkerRegistry.create()
            stale = await asyncio.to_thread(registry.find_stale, threshold)
        except Exception as e:
            logger.debug(f"Orphan reclaim sweep failed to query stale workers: {e}")
            return

        locally_connected = set(self._worker_names)
        for name in stale:
            if name in locally_connected:
                continue
            try:
                # Called directly on the event loop (not via to_thread) because
                # it sets asyncio.Events (_execution_events, _work_available),
                # which are not thread-safe. This mirrors the local eviction
                # path, which also invokes it synchronously.
                self._unclaim_worker_executions(name)
            except Exception as e:
                logger.warning(f"Failed to reclaim executions for orphaned worker {name}: {e}")

    async def _run_heartbeat_reaper(self):
        """Background task: two-phase eviction (stale → grace → evict) and offline cache pruning."""
        try:
            while True:
                await asyncio.sleep(self.heartbeat_interval)
                now = time.monotonic()

                for name, last_pong in list(self._worker_last_pong.items()):
                    if (now - last_pong) > self.heartbeat_timeout:
                        if name not in self._worker_stale_since:
                            self._worker_stale_since[name] = now
                            logger.warning(
                                f"Worker {name} missed heartbeat, marked STALE "
                                f"(grace period: {self.eviction_grace_period}s)",
                            )

                recovered = [
                    name
                    for name in list(self._worker_stale_since)
                    if name in self._worker_last_pong
                    and (now - self._worker_last_pong[name]) <= self.heartbeat_timeout
                ]
                for name in recovered:
                    self._worker_stale_since.pop(name, None)
                    logger.info(f"Worker {name} recovered from stale state")

                evicted = [
                    name
                    for name, since in list(self._worker_stale_since.items())
                    if (now - since) > self.eviction_grace_period
                ]
                for name in evicted:
                    self._worker_stale_since.pop(name, None)
                    logger.warning(
                        f"Worker {name} evicted (stale for >{self.eviction_grace_period}s)",
                    )
                    self._disconnect_worker(name, reason="evicted")
                    self._unclaim_worker_executions(name)

                await self._reclaim_orphaned_executions()

                expired = [
                    name
                    for name, since in self._worker_offline_since.items()
                    if (now - since) > self.offline_ttl
                ]
                for name in expired:
                    self._worker_offline_since.pop(name, None)
                    self._worker_cache.pop(name, None)
                    logger.debug(f"Pruned offline worker {name} (exceeded {self.offline_ttl}s TTL)")
        except asyncio.CancelledError:
            logger.info("Heartbeat reaper stopped")

    async def _scheduler_loop(self):
        """Main scheduler loop - checks for due schedules periodically"""
        schedule_manager = create_schedule_manager()

        try:
            while self.scheduler_running:
                try:
                    await asyncio.sleep(self.poll_interval)

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

                        # Trigger each due schedule
                        for schedule in due_schedules:
                            try:
                                await self._trigger_scheduled_workflow(schedule, current_time)
                            except Exception as e:
                                # The trigger path already recorded the failure before
                                # re-raising; recording here would double-count it.
                                logger.error(
                                    f"Failed to trigger schedule '{schedule.name}': {str(e)}",
                                    exc_info=True,
                                )

                except Exception as e:
                    logger.error(f"Error in scheduler cycle: {str(e)}", exc_info=True)

        except asyncio.CancelledError:
            logger.info("Scheduler loop cancelled")

    async def _trigger_scheduled_workflow(self, schedule, scheduled_time: datetime):
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

                registry = PrincipalRegistry(session_factory=self._get_db_session)
                db_auth_service = AuthService(
                    config=auth_config,
                    session_factory=self._get_db_session,
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
            ctx = self._create_execution(
                _sched_ns,
                schedule.workflow_name,
                schedule.input_data,
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

            sched_link_session = self._get_db_session()
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

    # ===========================================
    # End Scheduler Methods
    # ===========================================

    def _create_api(self) -> FastAPI:
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def lifespan(app: FastAPI):
            # Resolve / generate the bootstrap token here (not at module
            # construction) so merely creating the FastAPI app for tests or
            # OpenAPI generation does not have the side effect of creating
            # <home>/bootstrap-token. FastAPI guarantees the startup half of
            # lifespan runs before any request is dispatched.
            from flux.security.bootstrap_token import resolve_or_generate

            startup_settings = Configuration.get().settings
            token, _ = resolve_or_generate(
                home=startup_settings.home,
                configured=startup_settings.workers.bootstrap_token,
            )
            self._bootstrap_token = token

            yield
            await self._stop_scheduler()
            await self._stop_reaper()

            from flux.observability import shutdown as shutdown_observability

            shutdown_observability()

        api = FastAPI(
            title="Flux",
            version=self._get_version(),
            docs_url="/docs",
            lifespan=lifespan,
        )

        limiter = Limiter(key_func=get_remote_address)
        api.state.limiter = limiter
        api.add_middleware(SlowAPIMiddleware)
        api.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

        _cors_origins = Configuration.get().settings.cors_allow_origins
        _cors_credentials = Configuration.get().settings.cors_allow_credentials
        # Browsers reject `Access-Control-Allow-Origin: *` together with
        # credentials, and the combination is a CSRF footgun — force credentials
        # off whenever origins are wildcarded.
        if "*" in _cors_origins:
            _cors_credentials = False
        api.add_middleware(
            CORSMiddleware,
            allow_origins=_cors_origins,
            allow_credentials=_cors_credentials,
            allow_methods=["*"],
            allow_headers=["*"],
        )

        _MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

        @api.middleware("http")
        async def _enforce_anonymous_policy(request: Request, call_next):
            # Secure default: when authentication is disabled, refuse anonymous
            # state-changing requests unless the operator has explicitly accepted
            # anonymous access. Read-only requests (and all requests when auth is
            # enabled) are unaffected; per-route checks still apply in that case.
            auth = Configuration.get().settings.security.auth
            if (
                not auth.enabled
                and not auth.allow_anonymous
                and request.method in _MUTATING_METHODS
            ):
                return JSONResponse(
                    status_code=401,
                    content={
                        "detail": (
                            "Anonymous state-changing requests are disabled. Enable "
                            "authentication, or set "
                            "FLUX_SECURITY__AUTH__ALLOW_ANONYMOUS=true to explicitly "
                            "permit anonymous access."
                        ),
                    },
                )
            return await call_next(request)

        auth_config = Configuration.get().settings.security.auth
        from flux.security.principals import PrincipalRegistry

        principal_registry = PrincipalRegistry(session_factory=self._get_db_session)
        auth_service = AuthService(
            config=auth_config,
            session_factory=self._get_db_session,
            registry=principal_registry,
        )
        auth_service.seed_built_in_roles()
        init_auth_service(auth_service)

        if not auth_config.enabled and not Configuration.get().settings.debug:
            logger.critical(
                "Authentication is DISABLED. All requests are treated as the ANONYMOUS "
                "admin principal. This is not safe for production. Enable an auth "
                "provider via [flux.security.auth.oidc] or [flux.security.auth.api_keys] "
                "before exposing this server.",
            )

        from flux.observability import get_metrics, is_enabled

        if is_enabled():
            from flux.observability.middleware import MetricsMiddleware

            metrics = get_metrics()
            if metrics:
                api.add_middleware(MetricsMiddleware, metrics=metrics)

        self._register_system_routes(
            api,
            auth_config=auth_config,
            auth_service=auth_service,
            principal_registry=principal_registry,
            limiter=limiter,
        )

        self._register_workflow_routes(
            api,
            auth_config=auth_config,
            auth_service=auth_service,
            principal_registry=principal_registry,
            limiter=limiter,
        )

        self._register_worker_routes(
            api,
            auth_config=auth_config,
            auth_service=auth_service,
            principal_registry=principal_registry,
            limiter=limiter,
        )

        self._register_admin_routes(
            api,
            auth_config=auth_config,
            auth_service=auth_service,
            principal_registry=principal_registry,
            limiter=limiter,
        )

        self._register_schedule_routes(
            api,
            auth_config=auth_config,
            auth_service=auth_service,
            principal_registry=principal_registry,
            limiter=limiter,
        )

        self._register_execution_routes(
            api,
            auth_config=auth_config,
            auth_service=auth_service,
            principal_registry=principal_registry,
            limiter=limiter,
        )

        self._register_service_routes(
            api,
            auth_config=auth_config,
            auth_service=auth_service,
            principal_registry=principal_registry,
            limiter=limiter,
        )

        self._register_rbac_routes(
            api,
            auth_config=auth_config,
            auth_service=auth_service,
            principal_registry=principal_registry,
            limiter=limiter,
        )

        self._register_auth_routes(
            api,
            auth_config=auth_config,
            auth_service=auth_service,
            principal_registry=principal_registry,
            limiter=limiter,
        )

        return api


if __name__ == "__main__":  # pragma: no cover
    settings = Configuration.get().settings
    Server(settings.server_host, settings.server_port).start()
