from __future__ import annotations

import asyncio
import base64
import re
import time
from typing import Any, Literal
from collections.abc import AsyncIterator
from uuid import uuid4

import uvicorn
from fastapi import Body
from fastapi import Depends
from fastapi import FastAPI
from fastapi import File
from fastapi import Header
from fastapi import HTTPException
from fastapi import Query
from fastapi import Request
from fastapi import UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from pydantic import BaseModel, Field
from sse_starlette import EventSourceResponse
from flux import ExecutionContext
from flux.catalogs import WorkflowCatalog, WorkflowInfo
from flux.config import Configuration
from flux.workflow import workflow
from flux.context_managers import ContextManager
from flux.domain.events import ExecutionEvent, ExecutionEventType
from flux.errors import ExecutionContextNotFoundError, WorkerNotFoundError, WorkflowNotFoundError
from flux.utils import get_logger
from flux.secret_managers import SecretManager
from flux.servers.uvicorn_server import UvicornServer
from flux.servers.models import ExecutionContext as ExecutionContextDTO
from flux.utils import to_json
from flux.worker_registry import WorkerInfo
from flux.worker_registry import WorkerRegistry
from flux.schedule_manager import create_schedule_manager
from flux.domain.schedule import schedule_factory
from flux.security.auth_service import AuthService
from flux.security.dependencies import init_auth_service, get_identity, require_permission
from flux.security.identity import ANONYMOUS, FluxIdentity
from datetime import datetime, timezone

logger = get_logger(__name__)

MAX_WORKFLOW_UPLOAD_BYTES = 1_048_576  # 1 MiB — workflow sources should be small
SERVICE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def _rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={"detail": "Rate limit exceeded. Try again later."},
    )


class WorkerRuntimeModel(BaseModel):
    os_name: str
    os_version: str
    python_version: str


class WorkerGPUModel(BaseModel):
    name: str
    memory_total: float
    memory_available: float


class WorkerResourcesModel(BaseModel):
    cpu_total: float
    cpu_available: float
    memory_total: float
    memory_available: float
    disk_total: float
    disk_free: float
    gpus: list[WorkerGPUModel]


class WorkerRegistration(BaseModel):
    name: str
    runtime: WorkerRuntimeModel
    packages: list[dict[str, str]]
    resources: WorkerResourcesModel
    labels: dict[str, str] = Field(default_factory=dict)


class SecretRequest(BaseModel):
    """Model for secret creation/update requests"""

    name: str
    value: Any


class SecretResponse(BaseModel):
    """Model for secret responses"""

    name: str
    value: Any | None = None


class ConfigRequest(BaseModel):
    name: str
    value: Any


class ScheduleRequest(BaseModel):
    """Model for schedule creation/update requests"""

    workflow_name: str
    workflow_namespace: str | None = None
    name: str
    schedule_config: dict  # Schedule configuration (cron expression, interval, etc.)
    description: str | None = None
    input_data: Any | None = None
    run_as_service_account: str | None = None


class ScheduleResponse(BaseModel):
    """Model for schedule responses"""

    id: str
    workflow_id: str
    workflow_namespace: str
    workflow_name: str
    name: str
    description: str | None
    schedule_type: str
    status: str
    created_at: str
    updated_at: str
    last_run_at: str | None
    next_run_at: str | None
    run_count: int
    failure_count: int
    run_as_service_account: str | None = None


class ScheduleUpdateRequest(BaseModel):
    """Model for schedule update requests"""

    schedule_config: dict | None = None
    description: str | None = None
    input_data: Any | None = None
    run_as_service_account: str | None = None


class RoleRequest(BaseModel):
    name: str
    permissions: list[str]


class RoleUpdateRequest(BaseModel):
    add_permissions: list[str] | None = None
    remove_permissions: list[str] | None = None


class RoleCloneRequest(BaseModel):
    new_name: str


class APIKeyRequest(BaseModel):
    name: str
    expires_in_days: int | None = None


class TestTokenRequest(BaseModel):
    token: str


class PrincipalCreateRequest(BaseModel):
    subject: str
    type: str
    external_issuer: str | None = None
    display_name: str | None = None
    roles: list[str] = []


class PrincipalUpdateRequest(BaseModel):
    display_name: str | None = None
    enabled: bool | None = None


class RoleGrantRequest(BaseModel):
    role: str


class PrincipalResponse(BaseModel):
    id: str
    subject: str
    type: str
    external_issuer: str
    display_name: str | None
    enabled: bool
    roles: list[str]


# New response models for missing endpoints
class WorkflowVersionResponse(BaseModel):
    """Model for workflow version responses"""

    id: str
    name: str
    version: int


class ExecutionSummaryResponse(BaseModel):
    """Model for execution summary responses"""

    execution_id: str
    workflow_id: str
    workflow_namespace: str
    workflow_name: str
    state: str
    worker_name: str | None = None


class ExecutionListResponse(BaseModel):
    """Model for execution list responses"""

    executions: list[ExecutionSummaryResponse]
    total: int
    limit: int
    offset: int


class WorkerResponse(BaseModel):
    """Model for worker responses"""

    name: str
    status: str = "offline"
    runtime: WorkerRuntimeModel | None = None
    resources: WorkerResourcesModel | None = None
    packages: list[dict[str, str]] = Field(default_factory=list)
    labels: dict[str, str] = Field(default_factory=dict)


class HealthResponse(BaseModel):
    """Model for health check responses"""

    status: str
    database: bool
    version: str


class ScheduleHistoryEntry(BaseModel):
    """Model for schedule history entry"""

    execution_id: str
    workflow_name: str
    state: str
    started_at: str | None = None
    completed_at: str | None = None


class ScheduleHistoryResponse(BaseModel):
    """Model for schedule history responses"""

    schedule_id: str
    workflow_name: str
    entries: list[ScheduleHistoryEntry]
    total: int
    limit: int
    offset: int


class Server:
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
        return authorization.split(" ")[1]

    def _get_worker(self, name: str, authorization: str | None) -> WorkerInfo:
        token = self._extract_token(authorization)
        registry = WorkerRegistry.create()
        worker = registry.get(name)
        if worker.session_token != token:
            raise HTTPException(status_code=403, detail="Invalid token")
        return worker

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
    ) -> AsyncIterator[dict]:
        event = self._execution_events[ctx.execution_id]
        progress_buffer = self._progress_buffers.get(ctx.execution_id)
        try:
            while not ctx.has_finished:
                if progress_buffer:
                    progress_task = asyncio.create_task(progress_buffer.get())
                    checkpoint_task = asyncio.create_task(event.wait())

                    done, pending = await asyncio.wait(
                        {progress_task, checkpoint_task},
                        timeout=30.0,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for t in pending:
                        t.cancel()
                    if pending:
                        await asyncio.gather(*pending, return_exceptions=True)

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
                    except asyncio.TimeoutError:
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
            self._execution_events.pop(ctx.execution_id, None)
            self._progress_buffers.pop(ctx.execution_id, None)

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
                logger.info(
                    f"Unclaimed execution {ctx.execution_id} from evicted worker {worker_name}",
                )
                event = self._execution_events.get(ctx.execution_id)
                if event:
                    event.set()
            except Exception as e:
                logger.error(f"Failed to unclaim execution {ctx.execution_id}: {e}")

        self._work_available.set()

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

                    # Get due schedules
                    current_time = datetime.now(timezone.utc)
                    due_schedules = schedule_manager.get_due_schedules(current_time=current_time)

                    if due_schedules:
                        logger.info(f"Found {len(due_schedules)} due schedule(s)")

                    # Trigger each due schedule
                    for schedule in due_schedules:
                        try:
                            await self._trigger_scheduled_workflow(schedule, current_time)
                        except Exception as e:
                            logger.error(
                                f"Failed to trigger schedule '{schedule.name}': {str(e)}",
                                exc_info=True,
                            )
                            schedule.mark_failure()

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
                    schedule.mark_failure()
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
                    schedule.mark_failure()
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
                    schedule.mark_failure()
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
                    schedule.mark_failure()
                    return

            _sched_ns = schedule.workflow_namespace
            ctx = self._create_execution(
                _sched_ns,
                schedule.workflow_name,
                schedule.input_data,
            )

            if auth_config.enabled and sa_principal is not None:
                from flux.security.execution_token import mint_execution_token

                exec_token = mint_execution_token(
                    subject=sa_principal.subject,
                    principal_issuer="flux",
                    execution_id=ctx.execution_id,
                    on_behalf_of=f"schedule:{schedule.name}",
                )
                sched_token_session = self._get_db_session()
                try:
                    from flux.models import ExecutionContextModel as _ECM5

                    exec_row = sched_token_session.get(_ECM5, ctx.execution_id)
                    if exec_row:
                        exec_row.exec_token = exec_token
                        exec_row.scheduling_subject = sa_principal.subject
                        exec_row.scheduling_principal_issuer = "flux"
                        sched_token_session.commit()
                finally:
                    sched_token_session.close()

            # Update schedule tracking
            schedule.mark_run(scheduled_time)

            logger.info(
                f"Triggered execution '{ctx.execution_id}' for '{schedule.workflow_name}'",
            )

            from flux.observability import get_metrics

            m = get_metrics()
            if m:
                m.record_schedule_trigger(schedule.name, "success")

        except Exception as e:
            schedule.mark_failure()
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

        api.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

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

        from flux.observability import get_metrics, is_enabled

        if is_enabled():
            from flux.observability.middleware import MetricsMiddleware

            metrics = get_metrics()
            if metrics:
                api.add_middleware(MetricsMiddleware, metrics=metrics)

            # Prometheus /metrics endpoint
            obs_config = Configuration.get().settings.observability
            if obs_config.prometheus_enabled:
                from prometheus_client import REGISTRY, generate_latest

                @api.get("/metrics")
                async def metrics_endpoint():
                    from starlette.responses import Response

                    return Response(
                        content=generate_latest(REGISTRY),
                        media_type="text/plain; version=0.0.4; charset=utf-8",
                    )

        @api.post("/workflows")
        async def workflows_save(
            file: UploadFile = File(...),
            identity: FluxIdentity = Depends(get_identity),
        ):
            chunk_size = 64 * 1024
            source_buffer = bytearray()
            while True:
                chunk = await file.read(chunk_size)
                if not chunk:
                    break
                if len(source_buffer) + len(chunk) > MAX_WORKFLOW_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=(
                            f"Workflow source too large: more than "
                            f"{MAX_WORKFLOW_UPLOAD_BYTES} bytes"
                        ),
                    )
                source_buffer.extend(chunk)
            source = bytes(source_buffer)
            logger.info(f"Received file: {file.filename} with size: {len(source)} bytes:")
            try:
                logger.debug(f"Processing workflow file: {file.filename}")
                catalog = WorkflowCatalog.create()
                workflows = catalog.parse(source)

                if auth_service is not None and auth_config.enabled:
                    for wf in workflows:
                        required = f"workflow:{wf.namespace}:*:register"
                        if not await auth_service.is_authorized(identity, required):
                            raise HTTPException(
                                status_code=403,
                                detail=f"Permission denied: requires '{required}'",
                            )

                result = catalog.save(workflows)
                logger.debug(f"Saved workflows: {[w.qualified_name for w in workflows]}")

                self._auto_create_schedules_from_source(source, workflows)

                return result
            except SyntaxError as e:
                logger.error(f"Syntax error while saving workflow: {str(e)}")
                raise HTTPException(status_code=400, detail=str(e))
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error saving workflow: {str(e)}")
                raise HTTPException(status_code=500, detail=f"Error saving workflow: {str(e)}")

        @api.get("/namespaces")
        async def list_namespaces(
            identity: FluxIdentity = Depends(get_identity),
        ):
            try:
                catalog = WorkflowCatalog.create()
                visible: dict[str, int] = {}
                if auth_service is not None and auth_config.enabled:
                    permissions = await auth_service.resolve_permissions(identity)
                    for wf in catalog.all():
                        required = f"workflow:{wf.namespace}:{wf.name}:read"
                        if identity.has_permission(required, permissions):
                            visible[wf.namespace] = visible.get(wf.namespace, 0) + 1
                else:
                    for wf in catalog.all():
                        visible[wf.namespace] = visible.get(wf.namespace, 0) + 1
                return [{"namespace": ns, "workflow_count": n} for ns, n in sorted(visible.items())]
            except Exception as e:
                raise HTTPException(
                    status_code=500,
                    detail=f"Error listing namespaces: {str(e)}",
                )

        @api.get("/workflows")
        async def workflows_all(
            namespace: str | None = None,
            identity: FluxIdentity = Depends(get_identity),
        ):
            try:
                logger.debug("Fetching all workflows")
                catalog = WorkflowCatalog.create()
                workflows = catalog.all(namespace=namespace)
                if auth_service is not None and auth_config.enabled:
                    permissions = await auth_service.resolve_permissions(identity)
                    filtered = []
                    for w in workflows:
                        required = f"workflow:{w.namespace}:{w.name}:read"
                        if identity.has_permission(required, permissions):
                            filtered.append(w)
                    workflows = filtered
                result = [
                    {"namespace": w.namespace, "name": w.name, "version": w.version}
                    for w in workflows
                ]
                logger.debug(f"Found {len(result)} workflows")
                return result
            except Exception as e:
                logger.error(f"Error listing workflows: {str(e)}")
                raise HTTPException(status_code=500, detail=f"Error listing workflows: {str(e)}")

        @api.get("/workflows/{namespace}/{workflow_name}")
        async def workflows_get_ns(
            namespace: str,
            workflow_name: str,
            identity: FluxIdentity = Depends(get_identity),
        ):
            try:
                if auth_service is not None and auth_config.enabled:
                    if not await auth_service.is_authorized(
                        identity,
                        f"workflow:{namespace}:{workflow_name}:read",
                    ):
                        raise HTTPException(
                            status_code=403,
                            detail=f"Permission denied: requires 'workflow:{namespace}:{workflow_name}:read'",
                        )
                logger.debug(f"Fetching workflow: {namespace}/{workflow_name}")
                catalog = WorkflowCatalog.create()
                workflow = catalog.get(namespace, workflow_name)
                logger.debug(
                    f"Found workflow: {namespace}/{workflow_name} (version: {workflow.version})",
                )
                return workflow.to_dict()
            except WorkflowNotFoundError as e:
                raise HTTPException(status_code=404, detail=str(e))
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Error retrieving workflow: {str(e)}")

        @api.post("/workflows/{namespace}/{workflow_name}/run/{mode}")
        async def workflows_run_ns(
            namespace: str,
            workflow_name: str,
            input: Any = Body(None),
            mode: str = "async",
            detailed: bool = False,
            version: int | None = None,
            identity: FluxIdentity = Depends(get_identity),
        ):
            try:
                logger.debug(
                    f"Running workflow: {namespace}/{workflow_name} (version: {version or 'latest'}) "
                    f"| Mode: {mode} | Detailed: {detailed}",
                )
                logger.debug(f"Input: {to_json(input)}")

                if not workflow_name:
                    raise HTTPException(status_code=400, detail="Workflow name is required.")

                if mode not in ["sync", "async", "stream"]:
                    raise HTTPException(
                        status_code=400,
                        detail="Invalid mode. Use 'sync', 'async', or 'stream'.",
                    )

                if auth_service is not None and auth_config.enabled:
                    workflow_info = WorkflowCatalog.create().get(namespace, workflow_name, version)
                    result = await auth_service.authorize(
                        identity,
                        namespace,
                        workflow_name,
                        workflow_info.metadata or {},
                    )
                    if not result.ok:
                        raise HTTPException(
                            status_code=403,
                            detail={
                                "message": "Authorization denied",
                                "missing_permissions": result.missing_permissions,
                            },
                        )

                ctx = self._create_execution(namespace, workflow_name, input, version)
                manager = ContextManager.create()

                if identity and identity != ANONYMOUS and auth_config.enabled:
                    from flux.security.execution_token import mint_execution_token

                    principal_issuer = (identity.metadata or {}).get("issuer", "flux")
                    exec_token = mint_execution_token(
                        subject=identity.subject,
                        principal_issuer=principal_issuer,
                        execution_id=ctx.execution_id,
                        on_behalf_of=identity.subject,
                    )
                    token_session = self._get_db_session()
                    try:
                        from flux.models import ExecutionContextModel as _ECM3

                        exec_row = token_session.get(_ECM3, ctx.execution_id)
                        if exec_row:
                            exec_row.exec_token = exec_token
                            exec_row.scheduling_subject = identity.subject
                            exec_row.scheduling_principal_issuer = principal_issuer
                            token_session.commit()
                    finally:
                        token_session.close()
                logger.debug(
                    f"Created execution context: {ctx.execution_id} for workflow: {namespace}/{workflow_name}",
                )

                # Register execution event BEFORE notifying workers to avoid
                # race where worker checkpoints before event exists.
                if mode in ("sync", "stream"):
                    self._execution_events.setdefault(
                        ctx.execution_id,
                        asyncio.Event(),
                    )

                self._notify_next_worker()

                if mode == "sync":
                    event = self._execution_events[ctx.execution_id]
                    try:
                        while not ctx.has_finished:
                            try:
                                await asyncio.wait_for(event.wait(), timeout=30.0)
                            except asyncio.TimeoutError:
                                pass
                            event.clear()
                            ctx = manager.get(ctx.execution_id)
                    finally:
                        self._execution_events.pop(ctx.execution_id, None)

                if mode == "stream":
                    self._progress_buffers[ctx.execution_id] = asyncio.Queue(maxsize=10000)

                    return EventSourceResponse(
                        self._stream_execution_events(ctx, manager, detailed),
                        media_type="text/event-stream",
                        headers={
                            "Content-Type": "text/event-stream",
                            "Cache-Control": "no-cache",
                            "Connection": "keep-alive",
                        },
                    )

                dto = ExecutionContextDTO.from_domain(ctx)
                response = dto.summary() if not detailed else dto
                logger.debug(
                    f"Returning execution result for {ctx.execution_id} in state: {ctx.state.value}",
                )
                return response

            except WorkflowNotFoundError as e:
                logger.error(f"Workflow not found: {str(e)}")
                raise HTTPException(status_code=404, detail=str(e))
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error scheduling workflow {namespace}/{workflow_name}: {str(e)}")
                raise HTTPException(status_code=500, detail=f"Error scheduling workflow: {str(e)}")

        @api.post("/workflows/{namespace}/{workflow_name}/resume/{execution_id}/{mode}")
        async def workflows_resume_ns(
            namespace: str,
            workflow_name: str,
            execution_id: str,
            input: Any = Body(None),
            mode: str = "async",
            detailed: bool = False,
            identity: FluxIdentity = Depends(get_identity),
        ):
            try:
                logger.debug(
                    f"Resuming workflow: {namespace}/{workflow_name} | Execution ID: {execution_id} | Mode: {mode} | Detailed: {detailed}",
                )
                logger.debug(f"Input: {to_json(input)}")

                if not execution_id:
                    raise HTTPException(status_code=400, detail="Execution ID is required.")

                if mode not in ["sync", "async", "stream"]:
                    raise HTTPException(
                        status_code=400,
                        detail="Invalid mode. Use 'sync', 'async', or 'stream'.",
                    )

                if auth_service is not None and auth_config.enabled:
                    workflow_info = WorkflowCatalog.create().get(namespace, workflow_name)
                    result = await auth_service.authorize(
                        identity,
                        namespace,
                        workflow_name,
                        workflow_info.metadata or {},
                    )
                    if not result.ok:
                        raise HTTPException(
                            status_code=403,
                            detail={
                                "message": "Authorization denied",
                                "missing_permissions": result.missing_permissions,
                            },
                        )

                manager = ContextManager.create()

                ctx = manager.get(execution_id)

                if ctx is None:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Execution context with ID {execution_id} not found.",
                    )

                if identity and identity != ANONYMOUS and auth_config.enabled:
                    from flux.security.execution_token import mint_execution_token

                    principal_issuer = (identity.metadata or {}).get("issuer", "flux")
                    exec_token = mint_execution_token(
                        subject=identity.subject,
                        principal_issuer=principal_issuer,
                        execution_id=ctx.execution_id,
                        on_behalf_of=identity.subject,
                    )
                    resume_token_session = self._get_db_session()
                    try:
                        from flux.models import ExecutionContextModel as _ECM4

                        exec_row = resume_token_session.get(_ECM4, ctx.execution_id)
                        if exec_row:
                            exec_row.exec_token = exec_token
                            exec_row.scheduling_subject = identity.subject
                            exec_row.scheduling_principal_issuer = principal_issuer
                            resume_token_session.commit()
                    finally:
                        resume_token_session.close()

                if ctx.has_finished:
                    raise HTTPException(
                        status_code=400,
                        detail="Cannot resume a finished execution.",
                    )

                ctx.start_resuming(input)
                manager.save(ctx)

                from flux.observability import get_metrics as _get_resume_metrics

                _rm = _get_resume_metrics()
                if _rm:
                    _rm.record_resume_queued(ctx.workflow_namespace, ctx.workflow_name)

                logger.debug(
                    f"Resuming execution context: {ctx.execution_id} for workflow: {namespace}/{workflow_name}",
                )

                # Register execution event BEFORE notifying workers to avoid
                # race where worker checkpoints before event exists.
                if mode in ("sync", "stream"):
                    self._execution_events.setdefault(
                        ctx.execution_id,
                        asyncio.Event(),
                    )

                self._notify_next_worker()

                if mode == "sync":
                    event = self._execution_events[ctx.execution_id]
                    try:
                        while not ctx.has_finished:
                            try:
                                await asyncio.wait_for(event.wait(), timeout=30.0)
                            except asyncio.TimeoutError:
                                pass
                            event.clear()
                            ctx = manager.get(ctx.execution_id)
                    finally:
                        self._execution_events.pop(ctx.execution_id, None)

                if mode == "stream":
                    self._progress_buffers[ctx.execution_id] = asyncio.Queue(maxsize=10000)

                    return EventSourceResponse(
                        self._stream_execution_events(ctx, manager, detailed),
                        media_type="text/event-stream",
                        headers={
                            "Content-Type": "text/event-stream",
                            "Cache-Control": "no-cache",
                            "Connection": "keep-alive",
                        },
                    )

                dto = ExecutionContextDTO.from_domain(ctx)
                response = dto.summary() if not detailed else dto
                logger.debug(
                    f"Returning execution result for {ctx.execution_id} in state: {ctx.state.value}",
                )
                return response

            except WorkflowNotFoundError as e:
                logger.error(f"Workflow not found: {str(e)}")
                raise HTTPException(status_code=404, detail=str(e))
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error scheduling workflow {namespace}/{workflow_name}: {str(e)}")
                raise HTTPException(status_code=500, detail=f"Error scheduling workflow: {str(e)}")

        @api.get("/workflows/{namespace}/{workflow_name}/status/{execution_id}")
        async def workflows_status_ns(
            namespace: str,
            workflow_name: str,
            execution_id: str,
            detailed: bool = False,
            identity: FluxIdentity = Depends(get_identity),
        ):
            try:
                logger.debug(
                    f"Checking status for workflow: {namespace}/{workflow_name} | Execution ID: {execution_id}",
                )

                if auth_service is not None and auth_config.enabled:
                    if not await auth_service.is_authorized(
                        identity,
                        f"workflow:{namespace}:{workflow_name}:read",
                    ):
                        raise HTTPException(
                            status_code=403,
                            detail=f"Permission denied: requires 'workflow:{namespace}:{workflow_name}:read'",
                        )

                manager = ContextManager.create()
                context = manager.get(execution_id)
                dto = ExecutionContextDTO.from_domain(context)
                result = dto.summary() if not detailed else dto
                logger.debug(f"Status for {execution_id}: {context.state.value}")
                return result
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Error inspecting workflow: {str(e)}")

        @api.get("/workflows/{namespace}/{workflow_name}/cancel/{execution_id}")
        async def workflows_cancel_ns(
            namespace: str,
            workflow_name: str,
            execution_id: str,
            mode: str = "async",
            detailed: bool = False,
            identity: FluxIdentity = Depends(get_identity),
        ):
            try:
                logger.debug(
                    f"Cancelling workflow: {namespace}/{workflow_name} | Execution ID: {execution_id} | Mode: {mode}",
                )

                if auth_service is not None and auth_config.enabled:
                    if not await auth_service.is_authorized(
                        identity,
                        f"workflow:{namespace}:{workflow_name}:run",
                    ):
                        raise HTTPException(
                            status_code=403,
                            detail=f"Permission denied: requires 'workflow:{namespace}:{workflow_name}:run'",
                        )

                if not execution_id:
                    raise HTTPException(status_code=400, detail="Execution ID is required.")

                if mode and mode not in ["sync", "async"]:
                    raise HTTPException(
                        status_code=400,
                        detail="Invalid mode. Use 'sync', 'async'.",
                    )

                manager = ContextManager.create()
                ctx = manager.get(execution_id)

                if ctx.has_finished:
                    raise HTTPException(
                        status_code=400,
                        detail="Cannot cancel a finished execution.",
                    )

                ctx.start_cancel()
                manager.save(ctx)
                self._execution_queue_times.pop(execution_id, None)

                from flux.observability import get_metrics

                m = get_metrics()
                if m:
                    m.record_workflow_completed(
                        ctx.workflow_namespace,
                        workflow_name,
                        "cancel_requested",
                        0,
                    )

                # Register execution event BEFORE notifying workers to avoid
                # race where worker checkpoints before event exists.
                if mode == "sync":
                    self._execution_events.setdefault(
                        ctx.execution_id,
                        asyncio.Event(),
                    )

                self._notify_next_worker()

                if mode == "sync":
                    event = self._execution_events[ctx.execution_id]
                    try:
                        while not ctx.has_finished:
                            logger.debug(
                                f"Waiting for cancellation of {execution_id}, current state: {ctx.state.value}",
                            )
                            try:
                                await asyncio.wait_for(event.wait(), timeout=30.0)
                            except asyncio.TimeoutError:
                                pass
                            event.clear()
                            ctx = manager.get(ctx.execution_id)
                    finally:
                        self._execution_events.pop(ctx.execution_id, None)

                dto = ExecutionContextDTO.from_domain(ctx)
                result = dto.summary() if not detailed else dto
                logger.info(
                    f"Workflow {namespace}/{workflow_name} execution {execution_id} is {dto.state}.",
                )
                return result
            except WorkflowNotFoundError as e:
                logger.error(f"Workflow not found: {str(e)}")
                raise HTTPException(status_code=404, detail=str(e))
            except WorkerNotFoundError as e:
                logger.error(f"Worker not found: {str(e)}")
                raise HTTPException(status_code=404, detail=str(e))
            except HTTPException as he:
                logger.error(
                    f"HTTP error while cancelling workflow {namespace}/{workflow_name}: {str(he)}",
                )
                raise
            except Exception as e:
                logger.error(f"Error cancelling workflow {namespace}/{workflow_name}: {str(e)}")
                raise HTTPException(status_code=500, detail=f"Error cancelling workflow: {str(e)}")

        @api.post("/workers/register")
        async def workers_register(
            registration: WorkerRegistration = Body(...),
            authorization: str = Header(None),
        ):
            try:
                logger.debug(f"Worker registration request: {registration.name}")
                token = self._extract_token(authorization)
                settings = Configuration.get().settings
                if settings.workers.bootstrap_token != token:
                    logger.warning(f"Invalid bootstrap token for worker: {registration.name}")
                    raise HTTPException(
                        status_code=403,
                        detail="Invalid bootstrap token.",
                    )

                registry = WorkerRegistry.create()
                result = registry.register(
                    registration.name,
                    registration.runtime,
                    registration.packages,
                    registration.resources,
                    labels=registration.labels,
                )

                self._worker_cache[registration.name] = WorkerResponse(
                    name=registration.name,
                    status="online" if registration.name in self._worker_names else "offline",
                    runtime=WorkerRuntimeModel(
                        os_name=registration.runtime.os_name,
                        os_version=registration.runtime.os_version,
                        python_version=registration.runtime.python_version,
                    ),
                    resources=WorkerResourcesModel(
                        cpu_total=registration.resources.cpu_total,
                        cpu_available=registration.resources.cpu_available,
                        memory_total=registration.resources.memory_total,
                        memory_available=registration.resources.memory_available,
                        disk_total=registration.resources.disk_total,
                        disk_free=registration.resources.disk_free,
                        gpus=[
                            WorkerGPUModel(
                                name=g.name,
                                memory_total=g.memory_total,
                                memory_available=g.memory_available,
                            )
                            for g in registration.resources.gpus
                        ]
                        if registration.resources.gpus
                        else [],
                    ),
                    packages=[
                        {"name": p["name"], "version": p["version"]} for p in registration.packages
                    ]
                    if registration.packages
                    else [],
                    labels=registration.labels,
                )
                if registration.name in self._worker_names:
                    self._worker_offline_since.pop(registration.name, None)
                else:
                    self._worker_offline_since[registration.name] = time.monotonic()

                logger.info(f"Worker registered successfully: {registration.name}")
                logger.debug(
                    f"Worker details: OS: {registration.runtime.os_name} {registration.runtime.os_version}, "
                    f"Python: {registration.runtime.python_version}, "
                    f"Resources: CPU: {registration.resources.cpu_total}, "
                    f"Memory: {registration.resources.memory_total}",
                )

                from flux.observability import get_metrics

                m = get_metrics()
                if m:
                    m.record_worker_registered(registration.name)

                return result
            except HTTPException:
                raise
            except Exception as e:
                logger.error(
                    f"Worker registration failed for {registration.name}: {type(e).__name__}: {e}",
                    exc_info=True,
                )
                raise HTTPException(
                    status_code=400,
                    detail=str(e),
                )

        @api.post("/workers/{name}/pong")
        async def workers_pong(name: str, authorization: str = Header(None)):
            """Receive heartbeat pong from a worker."""
            try:
                self._get_worker(name, authorization)
                self._worker_last_pong[name] = time.monotonic()
                self._worker_stale_since.pop(name, None)
                logger.debug(f"Pong received from worker {name}")
                return {"status": "ok"}
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        @api.get("/workers/{name}/connect")
        async def workers_connect(name: str, authorization: str = Header(None)):
            try:
                logger.debug(f"Worker connection request: {name}")
                worker = self._get_worker(name, authorization)
                logger.info(f"Worker connected: {name}")

                worker_event = asyncio.Event()
                eviction_event = asyncio.Event()
                self._worker_events[name] = worker_event
                self._worker_evicted[name] = eviction_event
                if name not in self._worker_names:
                    self._worker_names.append(name)
                self._worker_last_pong[name] = time.monotonic()
                self._worker_stale_since.pop(name, None)
                self._worker_offline_since.pop(name, None)
                gen = self._worker_connection_gen.get(name, 0) + 1
                self._worker_connection_gen[name] = gen
                if name in self._worker_cache:
                    self._worker_cache[name].status = "online"

                from flux.observability import get_metrics as _get_metrics

                _m = _get_metrics()
                if _m:
                    _m.record_worker_connected(name)

                logger.debug(
                    f"Worker {name} registered for round-robin (total: {len(self._worker_names)})",
                )

                async def check_for_new_executions():
                    fallback_interval = 0.5
                    last_ping_time = time.monotonic()

                    context_manager = ContextManager.create()
                    try:
                        while True:
                            try:
                                if eviction_event.is_set():
                                    logger.info(f"Worker {name} evicted by reaper, closing SSE")
                                    return

                                now = time.monotonic()
                                if (now - last_ping_time) >= self.heartbeat_interval:
                                    last_ping_time = now
                                    yield {
                                        "event": "ping",
                                        "data": "",
                                    }

                                ctx = context_manager.next_execution(worker)
                                if ctx:
                                    _exec_ns = ctx.workflow_namespace
                                    workflow = WorkflowCatalog.create().get(
                                        _exec_ns,
                                        ctx.workflow_name,
                                    )
                                    workflow.source = base64.b64encode(workflow.source).decode(
                                        "utf-8",
                                    )

                                    logger.debug(
                                        f"Sending execution to worker {name}: {ctx.execution_id} (workflow: {ctx.workflow_name})",
                                    )

                                    payload_dict = {"workflow": workflow, "context": ctx}
                                    exec_model_session = self._get_db_session()
                                    try:
                                        from flux.models import ExecutionContextModel as _ECM

                                        exec_row = exec_model_session.get(
                                            _ECM,
                                            ctx.execution_id,
                                        )
                                        exec_token_for_dispatch = (
                                            exec_row.exec_token if exec_row else None
                                        )
                                    finally:
                                        exec_model_session.close()
                                    if exec_token_for_dispatch:
                                        payload_dict["exec_token"] = exec_token_for_dispatch
                                    data_payload = to_json(payload_dict)

                                    from flux.observability import is_enabled

                                    if is_enabled():
                                        import json as _json

                                        from flux.observability.tracing import inject_trace_context

                                        try:
                                            event_data = _json.loads(data_payload)
                                            event_data["trace_context"] = inject_trace_context()
                                            data_payload = _json.dumps(event_data)
                                        except Exception:
                                            pass

                                    yield {
                                        "id": f"{ctx.execution_id}_{uuid4().hex}",
                                        "event": "execution_scheduled",
                                        "data": data_payload,
                                    }
                                    logger.debug(
                                        f"Execution {ctx.execution_id} scheduled for worker {name}",
                                    )
                                    continue

                                ctx = context_manager.next_cancellation(worker)
                                if ctx:
                                    logger.debug(
                                        f"Sending cancellation to worker {name}: {ctx.execution_id} (workflow: {ctx.workflow_name})",
                                    )

                                    yield {
                                        "id": f"{ctx.execution_id}_{uuid4().hex}",
                                        "event": "execution_cancelled",
                                        "data": to_json({"context": ctx}),
                                    }

                                    logger.debug(
                                        f"Cancellation {ctx.execution_id} sent to worker {name}",
                                    )
                                    continue

                                ctx = context_manager.next_resume(worker)
                                if ctx:
                                    _resume_ns = ctx.workflow_namespace
                                    workflow = WorkflowCatalog.create().get(
                                        _resume_ns,
                                        ctx.workflow_name,
                                    )
                                    workflow.source = base64.b64encode(workflow.source).decode(
                                        "utf-8",
                                    )
                                    logger.debug(
                                        f"Sending resume to worker {name}: {ctx.execution_id} (workflow: {ctx.workflow_name})",
                                    )

                                    resume_payload_dict = {"workflow": workflow, "context": ctx}
                                    resume_model_session = self._get_db_session()
                                    try:
                                        from flux.models import ExecutionContextModel as _ECM2

                                        resume_exec_row = resume_model_session.get(
                                            _ECM2,
                                            ctx.execution_id,
                                        )
                                        resume_exec_token = (
                                            resume_exec_row.exec_token if resume_exec_row else None
                                        )
                                    finally:
                                        resume_model_session.close()
                                    if resume_exec_token:
                                        resume_payload_dict["exec_token"] = resume_exec_token
                                    yield {
                                        "id": f"{ctx.execution_id}_{uuid4().hex}",
                                        "event": "execution_resumed",
                                        "data": to_json(resume_payload_dict),
                                    }

                                    logger.debug(
                                        f"Resumption {ctx.execution_id} sent to worker {name}",
                                    )
                                    continue

                                # No work found — wait for per-worker signal or fallback
                                worker_event.clear()
                                self._work_available.clear()
                                try:
                                    worker_task = asyncio.ensure_future(worker_event.wait())
                                    broadcast_task = asyncio.ensure_future(
                                        self._work_available.wait(),
                                    )
                                    done, pending = await asyncio.wait(
                                        [worker_task, broadcast_task],
                                        timeout=fallback_interval,
                                        return_when=asyncio.FIRST_COMPLETED,
                                    )
                                    for task in pending:
                                        task.cancel()
                                        try:
                                            await task
                                        except asyncio.CancelledError:
                                            pass
                                except Exception:
                                    pass
                            except Exception as e:
                                logger.error(
                                    f"Error in worker connection stream for {name}: {str(e)}",
                                )
                                yield {
                                    "event": "error",
                                    "data": str(e),
                                }
                    finally:
                        if self._worker_connection_gen.get(name) == gen:
                            self._disconnect_worker(name)
                            logger.info(
                                f"Worker {name} disconnected (remaining: {len(self._worker_names)})",
                            )
                        else:
                            logger.debug(
                                f"Stale SSE for {name} closed (superseded by newer connection)",
                            )

                return EventSourceResponse(
                    check_for_new_executions(),
                    media_type="text/event-stream",
                    headers={
                        "Content-Type": "text/event-stream",
                        "Cache-Control": "no-cache",
                        "Connection": "keep-alive",
                    },
                )
            except Exception as e:
                raise HTTPException(status_code=404, detail=str(e))

        @api.post("/workers/{name}/claim/{execution_id}")
        async def workers_claim(name: str, execution_id: str, authorization: str = Header(None)):
            from flux.domain import ExecutionState

            try:
                logger.debug(f"Worker {name} claiming execution: {execution_id}")
                worker = self._get_worker(name, authorization)
                context_manager = ContextManager.create()

                try:
                    current = context_manager.get(execution_id)
                except ExecutionContextNotFoundError:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Execution {execution_id} not found.",
                    )

                if current.state in (ExecutionState.CREATED, ExecutionState.SCHEDULED):
                    ctx = context_manager.claim(execution_id, worker)
                elif current.state == ExecutionState.RESUME_SCHEDULED:
                    ctx = context_manager.claim_resume(execution_id, worker)
                else:
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            f"Cannot claim execution {execution_id}: "
                            f"current state is {current.state.value}"
                        ),
                    )

                logger.info(f"Execution {execution_id} claimed by worker {name}")

                from flux.observability import get_metrics

                m = get_metrics()
                if m:
                    queued_at = self._execution_queue_times.pop(execution_id, None)
                    schedule_to_start = time.monotonic() - queued_at if queued_at else None
                    m.record_execution_claimed(schedule_to_start)

                # Notify any waiting sync/stream endpoint
                event = self._execution_events.get(execution_id)
                if event:
                    event.set()

                return ctx.summary()
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error claiming execution {execution_id} by worker {name}: {str(e)}")
                raise HTTPException(status_code=500, detail=str(e))

        @api.post("/workers/{name}/checkpoint/{execution_id}")
        async def workers_checkpoint(
            name: str,
            execution_id: str,
            context: ExecutionContextDTO = Body(...),
            authorization: str = Header(None),
        ):
            try:
                logger.debug(
                    f"Checkpoint request from worker: {name} for execution: {execution_id}",
                )
                logger.debug(f"Execution state: {context.state}")

                self._get_worker(name, authorization)
                self._worker_last_pong[name] = time.monotonic()

                context_manager = ContextManager.create()
                domain_ctx = context.to_domain()

                try:
                    ctx = context_manager.update(domain_ctx)
                except ExecutionContextNotFoundError:
                    logger.warning(f"Execution context not found: {execution_id}")
                    raise HTTPException(status_code=404, detail="Execution context not found.")
                logger.debug(f"Checkpoint saved for {execution_id}, state: {ctx.state.value}")

                if ctx.has_finished:
                    self._execution_queue_times.pop(execution_id, None)

                # Notify any waiting sync/stream endpoint
                event = self._execution_events.get(execution_id)
                if event:
                    event.set()

                return ctx.summary()
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error checkpointing execution: {str(e)}")
                raise HTTPException(status_code=400, detail=str(e))

        @api.post("/workers/{name}/progress/{execution_id}")
        async def workers_progress(
            name: str,
            execution_id: str,
            events: list = Body(...),
            authorization: str = Header(None),
        ):
            self._get_worker(name, authorization)
            self._worker_last_pong[name] = time.monotonic()

            buffer = self._progress_buffers.get(execution_id)
            if not buffer:
                return {"status": "ok"}

            for event in events:
                progress_event = ExecutionEvent(
                    type=ExecutionEventType.TASK_PROGRESS,
                    source_id=event.get("task_id", ""),
                    name=event.get("task_name", ""),
                    value=event.get("value"),
                )
                try:
                    buffer.put_nowait(progress_event)
                except asyncio.QueueFull:
                    pass
            return {"status": "ok"}

        @api.get("/admin/secrets")
        async def admin_list_secrets(
            identity: FluxIdentity = Depends(require_permission("admin:secrets:read")),
        ):
            try:
                logger.info("Admin API: Listing all secrets")
                # List all secrets (names only for security)
                secret_manager = SecretManager.current()
                try:
                    # Use the new all() method to get all secret names
                    secret_names = secret_manager.all()
                    logger.info(f"Admin API: Successfully retrieved {len(secret_names)} secrets")
                    return secret_names
                except Exception as ex:
                    logger.error(f"Error listing secrets: {str(ex)}")
                    raise HTTPException(status_code=500, detail=f"Error listing secrets: {str(ex)}")
            except HTTPException:
                raise
            except Exception as ex:
                logger.error(f"Error listing secrets: {str(ex)}")
                raise HTTPException(status_code=500, detail=str(ex))

        @api.get("/admin/secrets/{name}")
        async def admin_get_secret(
            name: str,
            identity: FluxIdentity = Depends(require_permission("admin:secrets:read")),
        ):
            try:
                logger.info(f"Admin API: Getting secret '{name}'")

                # Get secret value
                secret_manager = SecretManager.current()
                try:
                    result = secret_manager.get([name])
                    logger.info(f"Admin API: Successfully retrieved secret '{name}'")
                    return SecretResponse(name=name, value=result[name])
                except ValueError:
                    logger.warning(f"Admin API: Secret not found: '{name}'")
                    raise HTTPException(status_code=404, detail=f"Secret not found: {name}")
                except Exception as ex:
                    logger.error(f"Admin API: Error retrieving secret '{name}': {str(ex)}")
                    raise HTTPException(
                        status_code=500,
                        detail=f"Error retrieving secret: {str(ex)}",
                    )
            except HTTPException:
                raise
            except Exception as ex:
                logger.error(f"Admin API: Error in admin_get_secret for '{name}': {str(ex)}")
                raise HTTPException(status_code=500, detail=str(ex))

        @api.post("/admin/secrets")
        async def admin_create_or_update_secret(
            secret: SecretRequest = Body(...),
            identity: FluxIdentity = Depends(require_permission("admin:secrets:manage")),
        ):
            try:
                logger.info(f"Admin API: Creating/updating secret '{secret.name}'")

                # Save secret
                secret_manager = SecretManager.current()
                try:
                    secret_manager.save(secret.name, secret.value)
                    logger.info(f"Admin API: Successfully saved secret '{secret.name}'")
                    return {
                        "status": "success",
                        "message": f"Secret '{secret.name}' saved successfully",
                    }
                except Exception as ex:
                    logger.error(f"Admin API: Error saving secret '{secret.name}': {str(ex)}")
                    raise HTTPException(status_code=500, detail=f"Error saving secret: {str(ex)}")
            except HTTPException:
                raise
            except Exception as ex:
                logger.error(
                    f"Admin API: Error in admin_create_or_update_secret for '{secret.name}': {str(ex)}",
                )
                raise HTTPException(status_code=500, detail=str(ex))

        @api.delete("/admin/secrets/{name}")
        async def admin_delete_secret(
            name: str,
            identity: FluxIdentity = Depends(require_permission("admin:secrets:manage")),
        ):
            try:
                logger.info(f"Admin API: Deleting secret '{name}'")

                # Remove secret
                secret_manager = SecretManager.current()
                try:
                    secret_manager.remove(name)
                    logger.info(f"Admin API: Successfully deleted secret '{name}'")
                    return {"status": "success", "message": f"Secret '{name}' deleted successfully"}
                except Exception as ex:
                    logger.error(f"Admin API: Error deleting secret '{name}': {str(ex)}")
                    raise HTTPException(status_code=500, detail=f"Error deleting secret: {str(ex)}")
            except HTTPException:
                raise
            except Exception as ex:
                logger.error(f"Admin API: Error in admin_delete_secret for '{name}': {str(ex)}")
                raise HTTPException(status_code=500, detail=str(ex))

        # Config API

        @api.get("/admin/configs")
        async def admin_list_configs(
            identity: FluxIdentity = Depends(require_permission("config:*:read")),
        ):
            from flux.config_manager import ConfigManager

            try:
                manager = ConfigManager.current()
                return manager.all()
            except Exception as ex:
                raise HTTPException(status_code=500, detail=str(ex))

        @api.get("/admin/configs/{name}")
        async def admin_get_config(
            name: str,
            identity: FluxIdentity = Depends(require_permission("config:*:read")),
        ):
            from flux.config_manager import ConfigManager

            try:
                manager = ConfigManager.current()
                result = manager.get([name])
                return {"name": name, "value": result[name]}
            except ValueError:
                raise HTTPException(status_code=404, detail=f"Config not found: {name}")
            except Exception as ex:
                raise HTTPException(status_code=500, detail=str(ex))

        @api.post("/admin/configs")
        async def admin_create_or_update_config(
            config_req: ConfigRequest = Body(...),
            identity: FluxIdentity = Depends(require_permission("config:*:manage")),
        ):
            from flux.config_manager import ConfigManager

            try:
                manager = ConfigManager.current()
                manager.save(config_req.name, config_req.value)
                return {
                    "status": "success",
                    "message": f"Config '{config_req.name}' saved successfully",
                }
            except Exception as ex:
                raise HTTPException(status_code=500, detail=str(ex))

        @api.delete("/admin/configs/{name}")
        async def admin_delete_config(
            name: str,
            identity: FluxIdentity = Depends(require_permission("config:*:manage")),
        ):
            from flux.config_manager import ConfigManager

            try:
                manager = ConfigManager.current()
                manager.remove(name)
                return {
                    "status": "success",
                    "message": f"Config '{name}' deleted successfully",
                }
            except Exception as ex:
                raise HTTPException(status_code=500, detail=str(ex))

        # Agent API

        @api.get("/admin/agents")
        async def admin_list_agents(
            identity: FluxIdentity = Depends(require_permission("agent:*:read")),
        ):
            from flux.agents.manager import AgentManager

            try:
                manager = AgentManager.current()
                agents = manager.list()
                return [a.model_dump() for a in agents]
            except Exception as ex:
                raise HTTPException(status_code=500, detail=str(ex))

        @api.get("/admin/agents/{name}")
        async def admin_get_agent(
            name: str,
            identity: FluxIdentity = Depends(require_permission("agent:*:read")),
        ):
            from flux.agents.manager import AgentManager

            try:
                manager = AgentManager.current()
                agent_def = manager.get(name)
                return agent_def.model_dump()
            except ValueError:
                raise HTTPException(status_code=404, detail=f"Agent not found: {name}")
            except Exception as ex:
                raise HTTPException(status_code=500, detail=str(ex))

        @api.post("/admin/agents")
        async def admin_create_agent(
            agent_data: dict = Body(...),
            identity: FluxIdentity = Depends(require_permission("agent:*:create")),
        ):
            from flux.agents.manager import AgentManager
            from flux.agents.types import AgentDefinition

            try:
                definition = AgentDefinition(**agent_data)
                manager = AgentManager.current()
                manager.create(definition)
                return {
                    "status": "success",
                    "message": f"Agent '{definition.name}' created successfully",
                }
            except ValueError as ex:
                raise HTTPException(status_code=409, detail=str(ex))
            except Exception as ex:
                raise HTTPException(status_code=500, detail=str(ex))

        @api.put("/admin/agents/{name}")
        async def admin_update_agent(
            name: str,
            agent_data: dict = Body(...),
            identity: FluxIdentity = Depends(require_permission("agent:*:update")),
        ):
            from flux.agents.manager import AgentManager
            from flux.agents.types import AgentDefinition

            try:
                agent_data["name"] = name
                definition = AgentDefinition(**agent_data)
                manager = AgentManager.current()
                manager.update(definition)
                return {
                    "status": "success",
                    "message": f"Agent '{name}' updated successfully",
                }
            except ValueError as ex:
                raise HTTPException(status_code=404, detail=str(ex))
            except Exception as ex:
                raise HTTPException(status_code=500, detail=str(ex))

        @api.delete("/admin/agents/{name}")
        async def admin_delete_agent(
            name: str,
            identity: FluxIdentity = Depends(require_permission("agent:*:delete")),
        ):
            from flux.agents.manager import AgentManager

            try:
                manager = AgentManager.current()
                manager.delete(name)
                return {
                    "status": "success",
                    "message": f"Agent '{name}' deleted successfully",
                }
            except ValueError as ex:
                raise HTTPException(status_code=404, detail=str(ex))
            except Exception as ex:
                raise HTTPException(status_code=500, detail=str(ex))

        # Scheduling API
        def _schedule_model_to_response(schedule) -> ScheduleResponse:
            """Convert ScheduleModel to ScheduleResponse"""
            return ScheduleResponse(
                id=schedule.id,
                workflow_id=schedule.workflow_id,
                workflow_namespace=schedule.workflow_namespace,
                workflow_name=schedule.workflow_name,
                name=schedule.name,
                description=schedule.description,
                schedule_type=schedule.schedule_type.value,
                status=schedule.status.value,
                created_at=schedule.created_at.isoformat(),
                updated_at=schedule.updated_at.isoformat(),
                last_run_at=schedule.last_run_at.isoformat() if schedule.last_run_at else None,
                next_run_at=schedule.next_run_at.isoformat() if schedule.next_run_at else None,
                run_count=schedule.run_count,
                failure_count=schedule.failure_count,
                run_as_service_account=getattr(schedule, "run_as_service_account", None),
            )

        def _resolve_schedule_id_or_name(schedule_id_or_name: str, schedule_manager):
            """
            Resolve schedule by ID or name.

            First tries to get by ID. If not found, tries to find by name.
            If the input looks like a name (contains underscore or dash), also search by name.

            Args:
                schedule_id_or_name: Either a UUID schedule ID or a schedule name
                schedule_manager: The schedule manager instance

            Returns:
                ScheduleModel if found, None otherwise
            """
            # First try getting by ID (UUID)
            schedule = schedule_manager.get_schedule(schedule_id_or_name)
            if schedule:
                return schedule

            # If not found and looks like it could be a name (not a UUID pattern),
            # search all schedules for a matching name
            if "_" in schedule_id_or_name or "-" in schedule_id_or_name:
                all_schedules = schedule_manager.list_schedules(active_only=False)
                for sched in all_schedules:
                    if sched.name == schedule_id_or_name:
                        return sched

            return None

        @api.post("/schedules", response_model=ScheduleResponse)
        async def create_schedule(
            request: ScheduleRequest,
            identity: FluxIdentity = Depends(require_permission("schedule:*:manage")),
        ):
            """Create a new schedule for a workflow"""
            try:
                logger.info(
                    f"Creating schedule '{request.name}' for workflow '{request.workflow_name}'",
                )

                if auth_config.enabled:
                    if not request.run_as_service_account:
                        raise HTTPException(
                            status_code=400,
                            detail="run_as_service_account is required when auth is enabled",
                        )
                    sa = None
                    if auth_service.principal_registry is not None:
                        sa = auth_service.principal_registry.find(
                            request.run_as_service_account,
                            "flux",
                        )
                    if sa is None:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Service account '{request.run_as_service_account}' not found",
                        )

                # Get workflow from catalog to ensure it exists
                from flux.catalogs import resolve_workflow_ref as _resolve_ref

                if request.workflow_namespace:
                    _sched_req_ns = request.workflow_namespace
                    _sched_req_name = request.workflow_name
                else:
                    _sched_req_ns, _sched_req_name = _resolve_ref(request.workflow_name)
                catalog = WorkflowCatalog.create()
                workflow_def = catalog.get(_sched_req_ns, _sched_req_name)
                if not workflow_def:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Workflow '{request.workflow_name}' not found",
                    )

                # Create schedule from configuration
                schedule = schedule_factory(request.schedule_config)

                # Create schedule via manager
                schedule_manager = create_schedule_manager()
                schedule_model = schedule_manager.create_schedule(
                    workflow_id=workflow_def.id,
                    workflow_namespace=_sched_req_ns,
                    workflow_name=_sched_req_name,
                    name=request.name,
                    schedule=schedule,
                    description=request.description,
                    input_data=request.input_data,
                    run_as_service_account=request.run_as_service_account,
                )

                logger.info(
                    f"Successfully created schedule '{request.name}' with ID '{schedule_model.id}'",
                )
                return _schedule_model_to_response(schedule_model)

            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error creating schedule: {str(e)}")
                raise HTTPException(status_code=500, detail=f"Error creating schedule: {str(e)}")

        @api.get("/schedules", response_model=list[ScheduleResponse])
        async def list_schedules(
            workflow_name: str | None = None,
            active_only: bool = True,
            limit: int | None = None,
            offset: int | None = None,
            identity: FluxIdentity = Depends(get_identity),
        ):
            """List schedules visible to the principal.

            Schedules are filtered per-principal: a schedule is returned only if
            the caller has ``workflow:{namespace}:{workflow_name}:read`` on its
            bound workflow. Optionally filtered by workflow_ref with pagination.
            """
            try:
                logger.debug(
                    f"Listing schedules (workflow: {workflow_name}, active_only: {active_only}, "
                    f"limit: {limit}, offset: {offset})",
                )

                schedule_manager = create_schedule_manager()

                if workflow_name:
                    # Get workflow to get its ID
                    from flux.catalogs import resolve_workflow_ref as _resolve_ref2

                    _list_sched_ns, _list_sched_name = _resolve_ref2(workflow_name)
                    catalog = WorkflowCatalog.create()
                    workflow_def = catalog.get(_list_sched_ns, _list_sched_name)
                    if not workflow_def:
                        raise HTTPException(
                            status_code=404,
                            detail=f"Workflow '{workflow_name}' not found",
                        )

                    schedules = schedule_manager.list_schedules(
                        workflow_id=workflow_def.id,
                        active_only=active_only,
                        limit=limit,
                        offset=offset,
                    )
                else:
                    schedules = schedule_manager.list_schedules(
                        active_only=active_only,
                        limit=limit,
                        offset=offset,
                    )

                # Per-principal filter: keep only schedules whose bound workflow
                # the caller has read access to.
                if auth_service is not None and auth_config.enabled:
                    permissions = await auth_service.resolve_permissions(identity)
                    visible = []
                    for s in schedules:
                        required = f"workflow:{s.workflow_namespace}:{s.workflow_name}:read"
                        if identity.has_permission(required, permissions):
                            visible.append(s)
                    schedules = visible

                result = [_schedule_model_to_response(s) for s in schedules]
                logger.debug(f"Found {len(result)} schedules")
                return result

            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error listing schedules: {str(e)}")
                raise HTTPException(status_code=500, detail=f"Error listing schedules: {str(e)}")

        @api.get("/schedules/{schedule_id}", response_model=ScheduleResponse)
        async def get_schedule(
            schedule_id: str,
            identity: FluxIdentity = Depends(get_identity),
        ):
            """Get a specific schedule by ID or name.

            Authorized only if the caller has
            ``workflow:{namespace}:{workflow_name}:read`` on the schedule's
            bound workflow.
            """
            try:
                logger.debug(f"Getting schedule '{schedule_id}'")

                schedule_manager = create_schedule_manager()

                # Resolve by ID or name
                schedule = _resolve_schedule_id_or_name(schedule_id, schedule_manager)
                if not schedule:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Schedule '{schedule_id}' not found",
                    )

                if auth_service is not None and auth_config.enabled:
                    required = (
                        f"workflow:{schedule.workflow_namespace}:" f"{schedule.workflow_name}:read"
                    )
                    if not await auth_service.is_authorized(identity, required):
                        raise HTTPException(
                            status_code=403,
                            detail=f"Permission denied: requires '{required}'",
                        )

                return _schedule_model_to_response(schedule)

            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error getting schedule: {str(e)}")
                raise HTTPException(status_code=500, detail=f"Error getting schedule: {str(e)}")

        @api.put("/schedules/{schedule_id}", response_model=ScheduleResponse)
        async def update_schedule(
            schedule_id: str,
            request: ScheduleUpdateRequest,
            identity: FluxIdentity = Depends(require_permission("schedule:*:manage")),
        ):
            """Update an existing schedule (accepts either schedule ID or name)"""
            try:
                logger.info(f"Updating schedule '{schedule_id}'")

                if auth_config.enabled and request.run_as_service_account is not None:
                    sa = None
                    if auth_service.principal_registry is not None:
                        sa = auth_service.principal_registry.find(
                            request.run_as_service_account,
                            "flux",
                        )
                    if sa is None:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Service account '{request.run_as_service_account}' not found",
                        )

                schedule_manager = create_schedule_manager()

                # Resolve by ID or name
                existing_schedule = _resolve_schedule_id_or_name(schedule_id, schedule_manager)
                if not existing_schedule:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Schedule '{schedule_id}' not found",
                    )

                # Build update parameters
                schedule_param = None
                if request.schedule_config is not None:
                    schedule_param = schedule_factory(request.schedule_config)

                # Update using the actual ID
                schedule = schedule_manager.update_schedule(
                    existing_schedule.id,
                    schedule=schedule_param,
                    description=request.description,
                    input_data=request.input_data,
                    run_as_service_account=request.run_as_service_account,
                )

                logger.info(f"Successfully updated schedule '{schedule_id}'")
                return _schedule_model_to_response(schedule)

            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error updating schedule: {str(e)}")
                raise HTTPException(status_code=500, detail=f"Error updating schedule: {str(e)}")

        @api.post("/schedules/{schedule_id}/pause", response_model=ScheduleResponse)
        async def pause_schedule(
            schedule_id: str,
            identity: FluxIdentity = Depends(require_permission("schedule:*:manage")),
        ):
            """Pause a schedule (accepts either schedule ID or name)"""
            try:
                logger.info(f"Pausing schedule '{schedule_id}'")

                schedule_manager = create_schedule_manager()

                # Resolve by ID or name
                schedule = _resolve_schedule_id_or_name(schedule_id, schedule_manager)
                if not schedule:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Schedule '{schedule_id}' not found",
                    )

                # Now pause using the actual ID
                schedule = schedule_manager.pause_schedule(schedule.id)

                logger.info(f"Successfully paused schedule '{schedule_id}'")
                return _schedule_model_to_response(schedule)

            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error pausing schedule: {str(e)}")
                raise HTTPException(status_code=500, detail=f"Error pausing schedule: {str(e)}")

        @api.post("/schedules/{schedule_id}/resume", response_model=ScheduleResponse)
        async def resume_schedule(
            schedule_id: str,
            identity: FluxIdentity = Depends(require_permission("schedule:*:manage")),
        ):
            """Resume a paused schedule (accepts either schedule ID or name)"""
            try:
                logger.info(f"Resuming schedule '{schedule_id}'")

                schedule_manager = create_schedule_manager()

                # Resolve by ID or name
                schedule = _resolve_schedule_id_or_name(schedule_id, schedule_manager)
                if not schedule:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Schedule '{schedule_id}' not found",
                    )

                # Now resume using the actual ID
                schedule = schedule_manager.resume_schedule(schedule.id)

                logger.info(f"Successfully resumed schedule '{schedule_id}'")
                return _schedule_model_to_response(schedule)

            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error resuming schedule: {str(e)}")
                raise HTTPException(status_code=500, detail=f"Error resuming schedule: {str(e)}")

        @api.delete("/schedules/{schedule_id}")
        async def delete_schedule(
            schedule_id: str,
            identity: FluxIdentity = Depends(require_permission("schedule:*:manage")),
        ):
            """Delete a schedule (accepts either schedule ID or name)"""
            try:
                logger.info(f"Deleting schedule '{schedule_id}'")

                schedule_manager = create_schedule_manager()

                # Resolve by ID or name
                schedule = _resolve_schedule_id_or_name(schedule_id, schedule_manager)
                if not schedule:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Schedule '{schedule_id}' not found",
                    )

                # Now delete using the actual ID
                success = schedule_manager.delete_schedule(schedule.id)

                if not success:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Schedule '{schedule_id}' not found",
                    )

                logger.info(f"Successfully deleted schedule '{schedule_id}'")
                return {
                    "status": "success",
                    "message": f"Schedule '{schedule_id}' deleted successfully",
                }

            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error deleting schedule: {str(e)}")
                raise HTTPException(status_code=500, detail=f"Error deleting schedule: {str(e)}")

        # ===========================================
        # Workflow Version Management Endpoints
        # ===========================================

        @api.delete("/workflows/{namespace}/{workflow_name}")
        async def workflow_delete_ns(
            namespace: str,
            workflow_name: str,
            version: int | None = None,
            identity: FluxIdentity = Depends(get_identity),
        ):
            """Delete workflow by namespace/name, optionally specific version."""
            if auth_service is not None and auth_config.enabled:
                required = f"workflow:{namespace}:*:register"
                if not await auth_service.is_authorized(identity, required):
                    raise HTTPException(
                        status_code=403,
                        detail=f"Permission denied: requires '{required}'",
                    )
            try:
                logger.info(
                    f"Deleting workflow '{namespace}/{workflow_name}'"
                    + (f" version {version}" if version else " (all versions)"),
                )

                catalog = WorkflowCatalog.create()

                try:
                    catalog.get(namespace, workflow_name, version)
                except WorkflowNotFoundError:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Workflow '{namespace}/{workflow_name}'"
                        + (f" version {version}" if version else "")
                        + " not found",
                    )

                catalog.delete(namespace, workflow_name, version)

                logger.info(f"Successfully deleted workflow '{namespace}/{workflow_name}'")
                return {
                    "status": "success",
                    "message": f"Workflow '{namespace}/{workflow_name}'"
                    + (f" version {version}" if version else " (all versions)")
                    + " deleted successfully",
                }

            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error deleting workflow: {str(e)}")
                raise HTTPException(
                    status_code=500,
                    detail=f"Error deleting workflow: {str(e)}",
                )

        @api.get(
            "/workflows/{namespace}/{workflow_name}/versions",
            response_model=list[WorkflowVersionResponse],
        )
        async def workflow_versions_ns(
            namespace: str,
            workflow_name: str,
            identity: FluxIdentity = Depends(get_identity),
        ):
            """List all versions of a workflow."""
            try:
                if auth_service is not None and auth_config.enabled:
                    if not await auth_service.is_authorized(
                        identity,
                        f"workflow:{namespace}:{workflow_name}:read",
                    ):
                        raise HTTPException(
                            status_code=403,
                            detail=f"Permission denied: requires 'workflow:{namespace}:{workflow_name}:read'",
                        )
                logger.debug(f"Fetching versions for workflow: {namespace}/{workflow_name}")

                catalog = WorkflowCatalog.create()
                versions = catalog.versions(namespace, workflow_name)

                if not versions:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Workflow '{namespace}/{workflow_name}' not found",
                    )

                result = [
                    WorkflowVersionResponse(
                        id=v.id,
                        name=v.name,
                        version=v.version,
                    )
                    for v in versions
                ]
                logger.debug(
                    f"Found {len(result)} versions for workflow '{namespace}/{workflow_name}'",
                )
                return result

            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error listing workflow versions: {str(e)}")
                raise HTTPException(
                    status_code=500,
                    detail=f"Error listing workflow versions: {str(e)}",
                )

        @api.get("/workflows/{namespace}/{workflow_name}/versions/{version}")
        async def workflow_version_get_ns(
            namespace: str,
            workflow_name: str,
            version: int,
            identity: FluxIdentity = Depends(get_identity),
        ):
            """Get specific workflow version."""
            try:
                if auth_service is not None and auth_config.enabled:
                    if not await auth_service.is_authorized(
                        identity,
                        f"workflow:{namespace}:{workflow_name}:read",
                    ):
                        raise HTTPException(
                            status_code=403,
                            detail=f"Permission denied: requires 'workflow:{namespace}:{workflow_name}:read'",
                        )
                logger.debug(f"Fetching workflow '{namespace}/{workflow_name}' version {version}")

                catalog = WorkflowCatalog.create()
                workflow = catalog.get(namespace, workflow_name, version)

                logger.debug(f"Found workflow '{namespace}/{workflow_name}' version {version}")
                return workflow.to_dict()

            except WorkflowNotFoundError as e:
                raise HTTPException(status_code=404, detail=str(e))
            except Exception as e:
                logger.error(f"Error retrieving workflow version: {str(e)}")
                raise HTTPException(
                    status_code=500,
                    detail=f"Error retrieving workflow version: {str(e)}",
                )

        # ===========================================
        # Execution Endpoints
        # ===========================================

        @api.get("/executions", response_model=ExecutionListResponse)
        async def executions_list(
            workflow_name: str | None = None,
            namespace: str | None = None,
            state: str | None = None,
            limit: int = 50,
            offset: int = 0,
            identity: FluxIdentity = Depends(require_permission("execution:*:read")),
        ):
            """List executions with optional filtering."""
            try:
                logger.debug(
                    f"Listing executions (namespace: {namespace}, workflow: {workflow_name}, "
                    f"state: {state}, limit: {limit}, offset: {offset})",
                )

                from flux.domain import ExecutionState

                # Parse state if provided
                state_filter = None
                if state:
                    try:
                        state_filter = ExecutionState(state.upper())
                    except ValueError:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Invalid state '{state}'. Valid states: "
                            + ", ".join([s.value for s in ExecutionState]),
                        )

                manager = ContextManager.create()
                executions, total = manager.list(
                    workflow_name=workflow_name,
                    workflow_namespace=namespace,
                    state=state_filter,
                    limit=limit,
                    offset=offset,
                )

                result = ExecutionListResponse(
                    executions=[
                        ExecutionSummaryResponse(
                            execution_id=ex.execution_id,
                            workflow_id=ex.workflow_id,
                            workflow_namespace=ex.workflow_namespace,
                            workflow_name=ex.workflow_name,
                            state=ex.state.value,
                            worker_name=ex.current_worker,
                        )
                        for ex in executions
                    ],
                    total=total,
                    limit=limit,
                    offset=offset,
                )

                logger.debug(f"Found {total} executions")
                return result

            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error listing executions: {str(e)}")
                raise HTTPException(
                    status_code=500,
                    detail=f"Error listing executions: {str(e)}",
                )

        @api.get("/executions/{execution_id}")
        async def execution_get(
            execution_id: str,
            detailed: bool = False,
            identity: FluxIdentity = Depends(require_permission("execution:*:read")),
        ):
            """Get execution by ID."""
            try:
                logger.debug(f"Fetching execution: {execution_id}")

                manager = ContextManager.create()
                ctx = manager.get(execution_id)

                dto = ExecutionContextDTO.from_domain(ctx)
                result = dto.summary() if not detailed else dto

                logger.debug(f"Found execution {execution_id} in state: {ctx.state.value}")
                return result

            except ExecutionContextNotFoundError:
                raise HTTPException(
                    status_code=404,
                    detail=f"Execution '{execution_id}' not found",
                )
            except Exception as e:
                logger.error(f"Error retrieving execution: {str(e)}")
                raise HTTPException(
                    status_code=500,
                    detail=f"Error retrieving execution: {str(e)}",
                )

        @api.get(
            "/workflows/{namespace}/{workflow_name}/executions",
            response_model=ExecutionListResponse,
        )
        async def workflow_executions_list_ns(
            namespace: str,
            workflow_name: str,
            state: str | None = None,
            limit: int = 50,
            offset: int = 0,
            identity: FluxIdentity = Depends(get_identity),
        ):
            """List executions for a specific workflow."""
            try:
                logger.debug(
                    f"Listing executions for workflow '{namespace}/{workflow_name}' "
                    f"(state: {state}, limit: {limit}, offset: {offset})",
                )

                if auth_service is not None and auth_config.enabled:
                    if not await auth_service.is_authorized(
                        identity,
                        f"workflow:{namespace}:{workflow_name}:read",
                    ):
                        raise HTTPException(
                            status_code=403,
                            detail=f"Permission denied: requires 'workflow:{namespace}:{workflow_name}:read'",
                        )

                from flux.domain import ExecutionState

                catalog = WorkflowCatalog.create()
                try:
                    catalog.get(namespace, workflow_name)
                except WorkflowNotFoundError:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Workflow '{namespace}/{workflow_name}' not found",
                    )

                state_filter = None
                if state:
                    try:
                        state_filter = ExecutionState(state.upper())
                    except ValueError:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Invalid state '{state}'. Valid states: "
                            + ", ".join([s.value for s in ExecutionState]),
                        )

                manager = ContextManager.create()
                executions, total = manager.list(
                    workflow_name=workflow_name,
                    workflow_namespace=namespace,
                    state=state_filter,
                    limit=limit,
                    offset=offset,
                )

                result = ExecutionListResponse(
                    executions=[
                        ExecutionSummaryResponse(
                            execution_id=ex.execution_id,
                            workflow_id=ex.workflow_id,
                            workflow_namespace=ex.workflow_namespace,
                            workflow_name=ex.workflow_name,
                            state=ex.state.value,
                            worker_name=ex.current_worker,
                        )
                        for ex in executions
                    ],
                    total=total,
                    limit=limit,
                    offset=offset,
                )

                logger.debug(f"Found {total} executions for workflow '{namespace}/{workflow_name}'")
                return result

            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error listing workflow executions: {str(e)}")
                raise HTTPException(
                    status_code=500,
                    detail=f"Error listing workflow executions: {str(e)}",
                )

        # ===========================================
        # Worker Management Endpoints
        # ===========================================

        def _worker_info_to_response(w: WorkerInfo) -> WorkerResponse:
            """Convert WorkerInfo to WorkerResponse."""
            status = "online" if w.name in self._worker_names else "offline"
            worker_response = WorkerResponse(
                name=w.name,
                status=status,
                packages=[{"name": p["name"], "version": p["version"]} for p in w.packages]
                if w.packages
                else [],
                labels=w.labels if isinstance(w.labels, dict) else {},
            )

            if w.runtime:
                worker_response.runtime = WorkerRuntimeModel(
                    os_name=w.runtime.os_name,
                    os_version=w.runtime.os_version,
                    python_version=w.runtime.python_version,
                )

            if w.resources:
                worker_response.resources = WorkerResourcesModel(
                    cpu_total=w.resources.cpu_total,
                    cpu_available=w.resources.cpu_available,
                    memory_total=w.resources.memory_total,
                    memory_available=w.resources.memory_available,
                    disk_total=w.resources.disk_total,
                    disk_free=w.resources.disk_free,
                    gpus=[
                        WorkerGPUModel(
                            name=g.name,
                            memory_total=g.memory_total,
                            memory_available=g.memory_available,
                        )
                        for g in w.resources.gpus
                    ]
                    if w.resources.gpus
                    else [],
                )

            return worker_response

        @api.get("/workers", response_model=list[WorkerResponse])
        async def workers_list(
            status: Literal["online", "offline"] | None = Query(None),
            identity: FluxIdentity = Depends(get_identity),
        ):
            """List workers from in-memory cache. Optional ?status=online|offline filter.

            Worker visibility is intentionally unpermissioned — any authenticated user
            may discover available workers. Sensitive details are not exposed.
            """
            try:
                logger.debug(f"Listing workers (filter={status})")

                if status == "online":
                    result = [
                        self._worker_cache[n] for n in self._worker_names if n in self._worker_cache
                    ]
                elif status == "offline":
                    result = [
                        self._worker_cache[n]
                        for n in self._worker_offline_since
                        if n in self._worker_cache
                    ]
                else:
                    result = list(self._worker_cache.values())

                logger.debug(f"Found {len(result)} workers")
                return result

            except Exception as e:
                logger.error(f"Error listing workers: {str(e)}")
                raise HTTPException(
                    status_code=500,
                    detail=f"Error listing workers: {str(e)}",
                )

        @api.get("/workers/{name}", response_model=WorkerResponse)
        async def worker_get(
            name: str,
            identity: FluxIdentity = Depends(get_identity),
        ):
            """Get worker details, from cache first, DB fallback."""
            try:
                logger.debug(f"Fetching worker: {name}")

                if name in self._worker_cache:
                    return self._worker_cache[name]

                # Fallback to DB for historical workers
                registry = WorkerRegistry.create()
                w = registry.get(name)

                logger.debug(f"Found worker: {name}")
                return _worker_info_to_response(w)

            except WorkerNotFoundError:
                raise HTTPException(
                    status_code=404,
                    detail=f"Worker '{name}' not found",
                )
            except Exception as e:
                logger.error(f"Error retrieving worker: {str(e)}")
                raise HTTPException(
                    status_code=500,
                    detail=f"Error retrieving worker: {str(e)}",
                )

        # ===========================================
        # Health & System Endpoints
        # ===========================================

        @api.get("/health", response_model=HealthResponse)
        async def health():
            """Health check endpoint."""
            try:
                logger.debug("Health check requested")

                # Check database connectivity
                catalog = WorkflowCatalog.create()
                db_healthy = catalog.health_check()

                status = "healthy" if db_healthy else "unhealthy"
                version = self._get_version()

                result = HealthResponse(
                    status=status,
                    database=db_healthy,
                    version=version,
                )

                logger.debug(f"Health check result: {status}")
                return result

            except Exception as e:
                logger.error(f"Health check failed: {str(e)}")
                return HealthResponse(
                    status="unhealthy",
                    database=False,
                    version=self._get_version(),
                )

        # ===========================================
        # Schedule History Endpoint
        # ===========================================

        @api.get("/schedules/{schedule_id}/history", response_model=ScheduleHistoryResponse)
        async def schedule_history(
            schedule_id: str,
            limit: int = 50,
            offset: int = 0,
            identity: FluxIdentity = Depends(require_permission("schedule:*:read")),
        ):
            """Get execution history for a schedule."""
            try:
                logger.debug(
                    f"Fetching history for schedule '{schedule_id}' "
                    f"(limit: {limit}, offset: {offset})",
                )

                schedule_manager = create_schedule_manager()

                # Resolve by ID or name
                schedule = _resolve_schedule_id_or_name(schedule_id, schedule_manager)
                if not schedule:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Schedule '{schedule_id}' not found",
                    )

                # Get execution history
                entries, total = schedule_manager.get_schedule_history(
                    schedule.id,
                    limit=limit,
                    offset=offset,
                )

                result = ScheduleHistoryResponse(
                    schedule_id=schedule.id,
                    workflow_name=schedule.workflow_name,
                    entries=[
                        ScheduleHistoryEntry(
                            execution_id=e["execution_id"],
                            workflow_name=e["workflow_name"],
                            state=e["state"],
                        )
                        for e in entries
                    ],
                    total=total,
                    limit=limit,
                    offset=offset,
                )

                logger.debug(f"Found {total} history entries for schedule '{schedule_id}'")
                return result

            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error getting schedule history: {str(e)}")
                raise HTTPException(
                    status_code=500,
                    detail=f"Error getting schedule history: {str(e)}",
                )

        # ===========================================
        # Services: CRUD
        # ===========================================

        @api.post("/services", status_code=201)
        async def create_service(
            request: Request,
            identity: FluxIdentity = Depends(get_identity),
        ):
            from json import JSONDecodeError

            from flux.service_store import ServiceStore

            try:
                body = await request.json()
            except (JSONDecodeError, ValueError):
                raise HTTPException(status_code=400, detail="Invalid JSON body")

            name = body.get("name")
            if not name or not isinstance(name, str):
                raise HTTPException(
                    status_code=400,
                    detail="Service name is required and must be a string",
                )
            if not SERVICE_NAME_RE.match(name):
                raise HTTPException(
                    status_code=400,
                    detail="Service name must be lowercase alphanumeric with hyphens/underscores (e.g. 'my-service-1')",
                )

            for field in ("namespaces", "workflows", "exclusions"):
                val = body.get(field, [])
                if not isinstance(val, list):
                    raise HTTPException(
                        status_code=400,
                        detail=f"'{field}' must be a list",
                    )
                if not all(isinstance(x, str) for x in val):
                    raise HTTPException(
                        status_code=400,
                        detail=f"'{field}' must be a list of strings",
                    )

            mcp_val = body.get("mcp_enabled", False)
            if not isinstance(mcp_val, bool):
                raise HTTPException(status_code=400, detail="mcp_enabled must be a boolean")

            try:
                store = ServiceStore()
                svc = store.create(
                    name=name,
                    namespaces=body.get("namespaces", []),
                    workflows=body.get("workflows", []),
                    exclusions=body.get("exclusions", []),
                    mcp_enabled=mcp_val,
                )
                return {
                    "id": svc.id,
                    "name": svc.name,
                    "namespaces": svc.namespaces,
                    "workflows": svc.workflows,
                    "exclusions": svc.exclusions,
                    "mcp_enabled": svc.mcp_enabled,
                    "created_at": svc.created_at.isoformat() if svc.created_at else None,
                    "updated_at": svc.updated_at.isoformat() if svc.updated_at else None,
                }
            except HTTPException:
                raise
            except ValueError as e:
                raise HTTPException(status_code=409, detail=str(e))
            except Exception as e:
                logger.error(f"Error creating service: {str(e)}")
                raise HTTPException(status_code=500, detail=f"Error creating service: {str(e)}")

        @api.get("/services")
        async def list_services(
            identity: FluxIdentity = Depends(get_identity),
        ):
            from flux.service_store import ServiceStore

            try:
                store = ServiceStore()
                services = store.list()
                return [
                    {
                        "id": svc.id,
                        "name": svc.name,
                        "namespaces": svc.namespaces,
                        "workflows": svc.workflows,
                        "exclusions": svc.exclusions,
                        "mcp_enabled": svc.mcp_enabled,
                        "created_at": svc.created_at.isoformat() if svc.created_at else None,
                        "updated_at": svc.updated_at.isoformat() if svc.updated_at else None,
                    }
                    for svc in services
                ]
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error listing services: {str(e)}")
                raise HTTPException(status_code=500, detail=f"Error listing services: {str(e)}")

        @api.get("/services/{service_name}")
        async def get_service(
            service_name: str,
            identity: FluxIdentity = Depends(get_identity),
        ):
            from flux.service_store import ServiceStore
            from flux.service_resolver import ServiceResolver, CollisionError

            try:
                store = ServiceStore()
                svc = store.get(service_name)
                if not svc:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Service '{service_name}' not found",
                    )

                catalog = WorkflowCatalog.create()
                resolver = ServiceResolver(catalog, store)

                result: dict[str, Any] = {
                    "id": svc.id,
                    "name": svc.name,
                    "namespaces": svc.namespaces,
                    "workflows": svc.workflows,
                    "exclusions": svc.exclusions,
                    "mcp_enabled": svc.mcp_enabled,
                    "created_at": svc.created_at.isoformat() if svc.created_at else None,
                    "updated_at": svc.updated_at.isoformat() if svc.updated_at else None,
                }

                try:
                    endpoints = resolver.resolve(service_name)
                    result["endpoints"] = [
                        {
                            "name": wf.name,
                            "namespace": wf.namespace,
                            "version": wf.version,
                            "input_schema": (wf.metadata or {}).get("input_schema"),
                            "description": (wf.metadata or {}).get("description"),
                        }
                        for wf in endpoints.values()
                    ]
                except CollisionError as ce:
                    result["endpoints"] = []
                    result["collision_warning"] = str(ce)

                return result

            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error getting service: {str(e)}")
                raise HTTPException(status_code=500, detail=f"Error getting service: {str(e)}")

        @api.put("/services/{service_name}")
        async def update_service(
            service_name: str,
            request: Request,
            identity: FluxIdentity = Depends(get_identity),
        ):
            from json import JSONDecodeError

            from flux.service_store import ServiceStore, ServiceNotFoundError

            try:
                body = await request.json()
            except (JSONDecodeError, ValueError):
                raise HTTPException(status_code=400, detail="Invalid JSON body")

            for field in (
                "add_namespaces",
                "add_workflows",
                "add_exclusions",
                "remove_namespaces",
                "remove_workflows",
                "remove_exclusions",
            ):
                val = body.get(field)
                if val is not None and not isinstance(val, list):
                    raise HTTPException(
                        status_code=400,
                        detail=f"'{field}' must be a list",
                    )
                if val is not None and not all(isinstance(x, str) for x in val):
                    raise HTTPException(
                        status_code=400,
                        detail=f"'{field}' must be a list of strings",
                    )

            mcp_val = body.get("mcp_enabled")
            if mcp_val is not None and not isinstance(mcp_val, bool):
                raise HTTPException(status_code=400, detail="mcp_enabled must be a boolean")

            try:
                store = ServiceStore()
                svc = store.update(
                    name=service_name,
                    add_namespaces=body.get("add_namespaces"),
                    add_workflows=body.get("add_workflows"),
                    add_exclusions=body.get("add_exclusions"),
                    remove_namespaces=body.get("remove_namespaces"),
                    remove_workflows=body.get("remove_workflows"),
                    remove_exclusions=body.get("remove_exclusions"),
                    mcp_enabled=mcp_val,
                )
                return {
                    "id": svc.id,
                    "name": svc.name,
                    "namespaces": svc.namespaces,
                    "workflows": svc.workflows,
                    "exclusions": svc.exclusions,
                    "mcp_enabled": svc.mcp_enabled,
                    "created_at": svc.created_at.isoformat() if svc.created_at else None,
                    "updated_at": svc.updated_at.isoformat() if svc.updated_at else None,
                }
            except HTTPException:
                raise
            except ServiceNotFoundError:
                raise HTTPException(
                    status_code=404,
                    detail=f"Service '{service_name}' not found",
                )
            except Exception as e:
                logger.error(f"Error updating service: {str(e)}")
                raise HTTPException(status_code=500, detail=f"Error updating service: {str(e)}")

        @api.delete("/services/{service_name}")
        async def delete_service(
            service_name: str,
            identity: FluxIdentity = Depends(get_identity),
        ):
            from flux.service_store import ServiceStore, ServiceNotFoundError

            try:
                store = ServiceStore()
                store.delete(service_name)
                return {"detail": f"Service '{service_name}' deleted"}
            except HTTPException:
                raise
            except ServiceNotFoundError:
                raise HTTPException(
                    status_code=404,
                    detail=f"Service '{service_name}' not found",
                )
            except Exception as e:
                logger.error(f"Error deleting service: {str(e)}")
                raise HTTPException(status_code=500, detail=f"Error deleting service: {str(e)}")

        @api.get("/services/{service_name}/mcp/tools")
        async def service_mcp_info(
            service_name: str,
            identity: FluxIdentity = Depends(get_identity),
        ):
            from flux.service_store import ServiceStore
            from flux.service_resolver import ServiceResolver, CollisionError

            try:
                store = ServiceStore()
                svc = store.get(service_name)
                if not svc or not svc.mcp_enabled:
                    raise HTTPException(
                        status_code=404,
                        detail=f"MCP not enabled for service '{service_name}'",
                    )

                catalog = WorkflowCatalog.create()
                resolver = ServiceResolver(catalog, store)

                try:
                    endpoints = resolver.resolve(service_name)
                except CollisionError:
                    raise HTTPException(
                        status_code=500,
                        detail=f"Endpoint collision in service '{service_name}'",
                    )

                tools = []
                for wf in endpoints.values():
                    name = wf.name
                    schema = (wf.metadata or {}).get("input_schema")
                    desc = (wf.metadata or {}).get("description", "")
                    tools.extend(
                        [
                            {
                                "name": name,
                                "description": f"Run {name} synchronously. {desc}".strip(),
                                "input_schema": schema,
                            },
                            {"name": f"{name}_async", "description": f"Run {name} asynchronously."},
                            {
                                "name": f"resume_{name}",
                                "description": f"Resume paused {name} synchronously.",
                            },
                            {
                                "name": f"resume_{name}_async",
                                "description": f"Resume paused {name} asynchronously.",
                            },
                            {
                                "name": f"status_{name}",
                                "description": f"Check {name} execution status.",
                            },
                        ],
                    )

                return {
                    "service": service_name,
                    "mcp_enabled": True,
                    "tools_url": f"/services/{service_name}/mcp/tools",
                    "tools": tools,
                    "tool_count": len(tools),
                }

            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error getting service MCP info: {str(e)}")
                raise HTTPException(
                    status_code=500,
                    detail=f"Error getting service MCP info: {str(e)}",
                )

        # ===========================================
        # Services: Execution endpoints
        # ===========================================

        def _service_detailed(ctx_dict, service_name, workflow_name, **extra):
            result = {
                "execution_id": ctx_dict.get("execution_id"),
                "state": ctx_dict.get("state"),
                "output": ctx_dict.get("output"),
                "namespace": ctx_dict.get("workflow_namespace"),
                "workflow": ctx_dict.get("workflow_name"),
            }
            result.update(extra)
            return result

        def _map_service_response(ctx_dict, service_name, workflow_name, mode, detailed):
            state = ctx_dict.get("state", "")
            exec_id = ctx_dict.get("execution_id")

            if mode == "async":
                return JSONResponse(
                    status_code=202,
                    content=_service_detailed(
                        ctx_dict,
                        service_name,
                        workflow_name,
                        status_url=f"/services/{service_name}/{workflow_name}/status/{exec_id}",
                    ),
                )

            if state == "COMPLETED":
                if detailed:
                    return JSONResponse(
                        status_code=200,
                        content=_service_detailed(ctx_dict, service_name, workflow_name),
                    )
                return JSONResponse(status_code=200, content=ctx_dict.get("output"))

            if state == "FAILED":
                content = _service_detailed(ctx_dict, service_name, workflow_name)
                content["error"] = str(ctx_dict.get("output", "Workflow failed"))
                return JSONResponse(status_code=500, content=content)

            if state == "PAUSED":
                return JSONResponse(
                    status_code=202,
                    content=_service_detailed(
                        ctx_dict,
                        service_name,
                        workflow_name,
                        resume_url=f"/services/{service_name}/{workflow_name}/resume/{exec_id}",
                    ),
                )

            return JSONResponse(status_code=200, content=ctx_dict)

        @api.post("/services/{service_name}/{workflow_name}")
        @api.post("/services/{service_name}/{workflow_name}/{mode}")
        async def service_run_workflow(
            service_name: str,
            workflow_name: str,
            input: Any = Body(None),
            mode: str = "sync",
            detailed: bool = False,
            version: int | None = None,
            identity: FluxIdentity = Depends(get_identity),
        ):
            from flux.service_resolver import (
                CollisionError,
                ServiceResolver,
                WorkflowNotInServiceError,
            )
            from flux.service_store import ServiceNotFoundError, ServiceStore

            try:
                if mode not in ["sync", "async", "stream"]:
                    raise HTTPException(
                        status_code=400,
                        detail="Invalid mode. Use 'sync', 'async', or 'stream'.",
                    )

                resolver = ServiceResolver(WorkflowCatalog.create(), ServiceStore())
                wf_info = resolver.find(service_name, workflow_name)
                namespace = wf_info.namespace

                if auth_service is not None and auth_config.enabled:
                    result = await auth_service.authorize(
                        identity,
                        namespace,
                        wf_info.name,
                        wf_info.metadata or {},
                    )
                    if not result.ok:
                        raise HTTPException(
                            status_code=403,
                            detail={
                                "message": "Authorization denied",
                                "missing_permissions": result.missing_permissions,
                            },
                        )

                ctx = self._create_execution(namespace, wf_info.name, input, version)
                manager = ContextManager.create()

                if identity and identity != ANONYMOUS and auth_config.enabled:
                    from flux.security.execution_token import mint_execution_token

                    principal_issuer = (identity.metadata or {}).get("issuer", "flux")
                    exec_token = mint_execution_token(
                        subject=identity.subject,
                        principal_issuer=principal_issuer,
                        execution_id=ctx.execution_id,
                        on_behalf_of=identity.subject,
                    )
                    token_session = self._get_db_session()
                    try:
                        from flux.models import ExecutionContextModel as _ECM_SVC

                        exec_row = token_session.get(_ECM_SVC, ctx.execution_id)
                        if exec_row:
                            exec_row.exec_token = exec_token
                            exec_row.scheduling_subject = identity.subject
                            exec_row.scheduling_principal_issuer = principal_issuer
                            token_session.commit()
                    finally:
                        token_session.close()

                if mode in ("sync", "stream"):
                    self._execution_events.setdefault(ctx.execution_id, asyncio.Event())

                self._notify_next_worker()

                if mode == "sync":
                    event = self._execution_events[ctx.execution_id]
                    try:
                        while not ctx.has_finished:
                            try:
                                await asyncio.wait_for(event.wait(), timeout=30.0)
                            except asyncio.TimeoutError:
                                pass
                            event.clear()
                            ctx = manager.get(ctx.execution_id)
                    finally:
                        self._execution_events.pop(ctx.execution_id, None)

                if mode == "stream":
                    self._progress_buffers[ctx.execution_id] = asyncio.Queue(maxsize=10000)
                    return EventSourceResponse(
                        self._stream_execution_events(ctx, manager, detailed),
                        media_type="text/event-stream",
                        headers={
                            "Content-Type": "text/event-stream",
                            "Cache-Control": "no-cache",
                            "Connection": "keep-alive",
                        },
                    )

                dto = ExecutionContextDTO.from_domain(ctx)
                ctx_dict = dto.model_dump() if hasattr(dto, "model_dump") else dto.dict()
                return _map_service_response(ctx_dict, service_name, workflow_name, mode, detailed)

            except ServiceNotFoundError:
                raise HTTPException(
                    status_code=404,
                    detail=f"Service '{service_name}' not found",
                )
            except WorkflowNotInServiceError:
                raise HTTPException(
                    status_code=404,
                    detail=f"Workflow '{workflow_name}' not found in service '{service_name}'",
                )
            except CollisionError as e:
                raise HTTPException(status_code=409, detail=str(e))
            except WorkflowNotFoundError as e:
                raise HTTPException(status_code=404, detail=str(e))
            except HTTPException:
                raise
            except Exception as e:
                logger.error(
                    f"Error running workflow via service {service_name}/{workflow_name}: {str(e)}",
                )
                raise HTTPException(
                    status_code=500,
                    detail=f"Error running workflow via service: {str(e)}",
                )

        @api.post("/services/{service_name}/{workflow_name}/resume/{execution_id}")
        @api.post("/services/{service_name}/{workflow_name}/resume/{execution_id}/{mode}")
        async def service_resume_workflow(
            service_name: str,
            workflow_name: str,
            execution_id: str,
            input: Any = Body(None),
            mode: str = "sync",
            detailed: bool = False,
            identity: FluxIdentity = Depends(get_identity),
        ):
            from flux.service_resolver import (
                CollisionError,
                ServiceResolver,
                WorkflowNotInServiceError,
            )
            from flux.service_store import ServiceNotFoundError, ServiceStore

            try:
                if mode not in ["sync", "async", "stream"]:
                    raise HTTPException(
                        status_code=400,
                        detail="Invalid mode. Use 'sync', 'async', or 'stream'.",
                    )

                resolver = ServiceResolver(WorkflowCatalog.create(), ServiceStore())
                wf_info = resolver.find(service_name, workflow_name)
                namespace = wf_info.namespace

                if auth_service is not None and auth_config.enabled:
                    result = await auth_service.authorize(
                        identity,
                        namespace,
                        wf_info.name,
                        wf_info.metadata or {},
                    )
                    if not result.ok:
                        raise HTTPException(
                            status_code=403,
                            detail={
                                "message": "Authorization denied",
                                "missing_permissions": result.missing_permissions,
                            },
                        )

                manager = ContextManager.create()
                ctx = manager.get(execution_id)

                if ctx is None:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Execution context with ID {execution_id} not found.",
                    )

                if (
                    getattr(ctx, "workflow_namespace", None) != wf_info.namespace
                    or getattr(ctx, "workflow_name", None) != wf_info.name
                ):
                    raise HTTPException(
                        status_code=404,
                        detail=f"Execution '{execution_id}' does not belong to workflow {wf_info.namespace}/{wf_info.name}",
                    )

                if identity and identity != ANONYMOUS and auth_config.enabled:
                    from flux.security.execution_token import mint_execution_token

                    principal_issuer = (identity.metadata or {}).get("issuer", "flux")
                    exec_token = mint_execution_token(
                        subject=identity.subject,
                        principal_issuer=principal_issuer,
                        execution_id=ctx.execution_id,
                        on_behalf_of=identity.subject,
                    )
                    resume_token_session = self._get_db_session()
                    try:
                        from flux.models import ExecutionContextModel as _ECM_SVC2

                        exec_row = resume_token_session.get(_ECM_SVC2, ctx.execution_id)
                        if exec_row:
                            exec_row.exec_token = exec_token
                            exec_row.scheduling_subject = identity.subject
                            exec_row.scheduling_principal_issuer = principal_issuer
                            resume_token_session.commit()
                    finally:
                        resume_token_session.close()

                if ctx.has_finished:
                    raise HTTPException(
                        status_code=400,
                        detail="Cannot resume a finished execution.",
                    )

                ctx.start_resuming(input)
                manager.save(ctx)

                from flux.observability import get_metrics as _get_resume_metrics

                _rm = _get_resume_metrics()
                if _rm:
                    _rm.record_resume_queued(ctx.workflow_namespace, ctx.workflow_name)

                if mode in ("sync", "stream"):
                    self._execution_events.setdefault(ctx.execution_id, asyncio.Event())

                self._notify_next_worker()

                if mode == "sync":
                    event = self._execution_events[ctx.execution_id]
                    try:
                        while not ctx.has_finished:
                            try:
                                await asyncio.wait_for(event.wait(), timeout=30.0)
                            except asyncio.TimeoutError:
                                pass
                            event.clear()
                            ctx = manager.get(ctx.execution_id)
                    finally:
                        self._execution_events.pop(ctx.execution_id, None)

                if mode == "stream":
                    self._progress_buffers[ctx.execution_id] = asyncio.Queue(maxsize=10000)
                    return EventSourceResponse(
                        self._stream_execution_events(ctx, manager, detailed),
                        media_type="text/event-stream",
                        headers={
                            "Content-Type": "text/event-stream",
                            "Cache-Control": "no-cache",
                            "Connection": "keep-alive",
                        },
                    )

                dto = ExecutionContextDTO.from_domain(ctx)
                ctx_dict = dto.model_dump() if hasattr(dto, "model_dump") else dto.dict()
                return _map_service_response(ctx_dict, service_name, workflow_name, mode, detailed)

            except ServiceNotFoundError:
                raise HTTPException(
                    status_code=404,
                    detail=f"Service '{service_name}' not found",
                )
            except WorkflowNotInServiceError:
                raise HTTPException(
                    status_code=404,
                    detail=f"Workflow '{workflow_name}' not found in service '{service_name}'",
                )
            except CollisionError as e:
                raise HTTPException(status_code=409, detail=str(e))
            except HTTPException:
                raise
            except Exception as e:
                logger.error(
                    f"Error resuming workflow via service {service_name}/{workflow_name}: {str(e)}",
                )
                raise HTTPException(
                    status_code=500,
                    detail=f"Error resuming workflow via service: {str(e)}",
                )

        @api.get("/services/{service_name}/{workflow_name}/status/{execution_id}")
        async def service_workflow_status(
            service_name: str,
            workflow_name: str,
            execution_id: str,
            identity: FluxIdentity = Depends(get_identity),
        ):
            from flux.service_resolver import (
                CollisionError,
                ServiceResolver,
                WorkflowNotInServiceError,
            )
            from flux.service_store import ServiceNotFoundError, ServiceStore

            try:
                resolver = ServiceResolver(WorkflowCatalog.create(), ServiceStore())
                wf_info = resolver.find(service_name, workflow_name)
                namespace = wf_info.namespace

                if auth_service is not None and auth_config.enabled:
                    if not await auth_service.is_authorized(
                        identity,
                        f"workflow:{namespace}:{wf_info.name}:read",
                    ):
                        raise HTTPException(
                            status_code=403,
                            detail=f"Permission denied: requires 'workflow:{namespace}:{wf_info.name}:read'",
                        )

                manager = ContextManager.create()
                context = manager.get(execution_id)
                if context is None:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Execution '{execution_id}' not found",
                    )

                if (
                    getattr(context, "workflow_namespace", None) != wf_info.namespace
                    or getattr(context, "workflow_name", None) != wf_info.name
                ):
                    raise HTTPException(
                        status_code=404,
                        detail=f"Execution '{execution_id}' does not belong to workflow {wf_info.namespace}/{wf_info.name}",
                    )

                dto = ExecutionContextDTO.from_domain(context)
                summary = dto.summary()
                return JSONResponse(
                    status_code=200,
                    content=_service_detailed(summary, service_name, workflow_name),
                )

            except ServiceNotFoundError:
                raise HTTPException(
                    status_code=404,
                    detail=f"Service '{service_name}' not found",
                )
            except WorkflowNotInServiceError:
                raise HTTPException(
                    status_code=404,
                    detail=f"Workflow '{workflow_name}' not found in service '{service_name}'",
                )
            except CollisionError as e:
                raise HTTPException(status_code=409, detail=str(e))
            except HTTPException:
                raise
            except Exception as e:
                logger.error(
                    f"Error checking status via service {service_name}/{workflow_name}: {str(e)}",
                )
                raise HTTPException(
                    status_code=500,
                    detail=f"Error checking workflow status via service: {str(e)}",
                )

        # ===========================================
        # Auth & Admin: Roles
        # ===========================================

        @api.get("/admin/roles")
        async def admin_list_roles(
            identity: FluxIdentity = Depends(require_permission("admin:roles:read")),
        ):
            try:
                roles = await auth_service.list_roles()
                return [
                    {
                        "name": r.name,
                        "permissions": r.permissions,
                        "built_in": r.built_in,
                    }
                    for r in roles
                ]
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        @api.get("/admin/roles/{name}")
        async def admin_get_role(
            name: str,
            identity: FluxIdentity = Depends(require_permission("admin:roles:read")),
        ):
            try:
                role = await auth_service.get_role(name)
                if not role:
                    raise HTTPException(status_code=404, detail=f"Role '{name}' not found")
                return {
                    "name": role.name,
                    "permissions": role.permissions,
                    "built_in": role.built_in,
                }
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        @api.post("/admin/roles")
        async def admin_create_role(
            request: RoleRequest,
            identity: FluxIdentity = Depends(require_permission("admin:roles:manage")),
        ):
            try:
                role = await auth_service.create_role(request.name, request.permissions)
                return {
                    "name": role.name,
                    "permissions": role.permissions,
                    "built_in": role.built_in,
                }
            except ValueError as e:
                msg = str(e)
                if "already exists" in msg.lower():
                    raise HTTPException(status_code=409, detail=msg)
                raise HTTPException(status_code=400, detail=msg)
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        @api.patch("/admin/roles/{name}")
        async def admin_update_role(
            name: str,
            request: RoleUpdateRequest,
            identity: FluxIdentity = Depends(require_permission("admin:roles:manage")),
        ):
            try:
                role = await auth_service.update_role(
                    name,
                    add_permissions=request.add_permissions,
                    remove_permissions=request.remove_permissions,
                )
                return {
                    "name": role.name,
                    "permissions": role.permissions,
                    "built_in": role.built_in,
                }
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e))
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        @api.delete("/admin/roles/{name}")
        async def admin_delete_role(
            name: str,
            identity: FluxIdentity = Depends(require_permission("admin:roles:manage")),
        ):
            try:
                await auth_service.delete_role(name)
                return {"status": "success", "message": f"Role '{name}' deleted"}
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e))
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        @api.post("/admin/roles/{name}/clone")
        async def admin_clone_role(
            name: str,
            request: RoleCloneRequest,
            identity: FluxIdentity = Depends(require_permission("admin:roles:manage")),
        ):
            try:
                role = await auth_service.clone_role(name, request.new_name)
                return {
                    "name": role.name,
                    "permissions": role.permissions,
                    "built_in": role.built_in,
                }
            except ValueError as e:
                msg = str(e)
                if "already exists" in msg.lower():
                    raise HTTPException(status_code=409, detail=msg)
                raise HTTPException(status_code=400, detail=msg)
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        # ===========================================
        # Auth & Admin: Principals
        # ===========================================

        @api.get("/admin/principals")
        async def admin_list_principals(
            type: str | None = None,
            identity: FluxIdentity = Depends(require_permission("admin:principals:read")),
        ):
            try:
                from flux.security.principals import PrincipalRegistry

                registry = PrincipalRegistry(session_factory=self._get_db_session)
                principals = registry.list_all(type=type)
                return [
                    {
                        "id": str(p.id),
                        "subject": p.subject,
                        "type": p.type,
                        "external_issuer": p.external_issuer,
                        "display_name": p.display_name,
                        "enabled": p.enabled,
                        "roles": registry.get_roles(p.id),
                    }
                    for p in principals
                ]
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        @api.get("/admin/principals/{subject}")
        async def admin_get_principal(
            subject: str,
            issuer: str | None = None,
            identity: FluxIdentity = Depends(require_permission("admin:principals:read")),
        ):
            try:
                from flux.security.principals import PrincipalRegistry

                registry = PrincipalRegistry(session_factory=self._get_db_session)
                principal = registry.find(subject, issuer or "flux")
                if not principal and issuer is None:
                    principal = registry.find(subject, "flux")
                if not principal:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Principal '{subject}' not found",
                    )
                return {
                    "id": str(principal.id),
                    "subject": principal.subject,
                    "type": principal.type,
                    "external_issuer": principal.external_issuer,
                    "display_name": principal.display_name,
                    "enabled": principal.enabled,
                    "roles": registry.get_roles(principal.id),
                }
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        @api.post("/admin/principals", status_code=201)
        async def admin_create_principal(
            request: PrincipalCreateRequest,
            identity: FluxIdentity = Depends(require_permission("admin:principals:manage")),
        ):
            try:
                from flux.security.principals import PrincipalRegistry

                registry = PrincipalRegistry(session_factory=self._get_db_session)
                external_issuer = request.external_issuer or (
                    "flux" if request.type == "service_account" else "flux"
                )
                principal = registry.create(
                    type=request.type,
                    subject=request.subject,
                    external_issuer=external_issuer,
                    display_name=request.display_name,
                )
                for role in request.roles:
                    registry.assign_role(principal.id, role)
                return {
                    "id": str(principal.id),
                    "subject": principal.subject,
                    "type": principal.type,
                    "external_issuer": principal.external_issuer,
                    "display_name": principal.display_name,
                    "enabled": principal.enabled,
                    "roles": request.roles,
                }
            except ValueError as e:
                raise HTTPException(status_code=409, detail=str(e))
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        @api.patch("/admin/principals/{subject}")
        async def admin_update_principal(
            subject: str,
            request: PrincipalUpdateRequest,
            issuer: str | None = None,
            identity: FluxIdentity = Depends(require_permission("admin:principals:manage")),
        ):
            try:
                from flux.security.principals import PrincipalRegistry

                registry = PrincipalRegistry(session_factory=self._get_db_session)
                principal = registry.find(subject, issuer or "flux")
                if not principal:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Principal '{subject}' not found",
                    )
                if request.display_name is not None:
                    registry.update_metadata(principal.id, display_name=request.display_name)
                if request.enabled is not None:
                    registry.set_enabled(principal.id, request.enabled)
                updated = registry.get(principal.id) or principal
                return {
                    "id": str(updated.id),
                    "subject": updated.subject,
                    "display_name": updated.display_name,
                    "enabled": updated.enabled,
                }
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        @api.delete("/admin/principals/{subject}", status_code=200)
        async def admin_delete_principal(
            subject: str,
            issuer: str | None = None,
            force: bool = False,
            identity: FluxIdentity = Depends(require_permission("admin:principals:manage")),
        ):
            try:
                from flux.security.principals import PrincipalRegistry

                registry = PrincipalRegistry(session_factory=self._get_db_session)
                principal = registry.find(subject, issuer or "flux")
                if not principal:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Principal '{subject}' not found",
                    )
                registry.delete(principal.id, force=force)
                return {"status": "success", "message": f"Principal '{subject}' deleted"}
            except ValueError as e:
                raise HTTPException(status_code=409, detail=str(e))
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        @api.post("/admin/principals/{subject}/roles")
        async def admin_grant_principal_role(
            subject: str,
            request: RoleGrantRequest,
            issuer: str | None = None,
            identity: FluxIdentity = Depends(require_permission("admin:principals:manage")),
        ):
            try:
                from flux.security.principals import PrincipalRegistry

                registry = PrincipalRegistry(session_factory=self._get_db_session)
                principal = registry.find(subject, issuer or "flux")
                if not principal:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Principal '{subject}' not found",
                    )
                registry.assign_role(principal.id, request.role, assigned_by=identity.subject)
                return {"status": "success", "role": request.role}
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e))
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        @api.delete("/admin/principals/{subject}/roles/{role_name}")
        async def admin_revoke_principal_role(
            subject: str,
            role_name: str,
            issuer: str | None = None,
            identity: FluxIdentity = Depends(require_permission("admin:principals:manage")),
        ):
            try:
                from flux.security.principals import PrincipalRegistry

                registry = PrincipalRegistry(session_factory=self._get_db_session)
                principal = registry.find(subject, issuer or "flux")
                if not principal:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Principal '{subject}' not found",
                    )
                registry.revoke_role(principal.id, role_name)
                return {"status": "success", "role": role_name}
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e))
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        @api.post("/admin/principals/{subject}/enable")
        async def admin_enable_principal(
            subject: str,
            issuer: str | None = None,
            identity: FluxIdentity = Depends(require_permission("admin:principals:manage")),
        ):
            try:
                from flux.security.principals import PrincipalRegistry

                registry = PrincipalRegistry(session_factory=self._get_db_session)
                principal = registry.find(subject, issuer or "flux")
                if not principal:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Principal '{subject}' not found",
                    )
                registry.set_enabled(principal.id, True)
                return {"status": "success", "subject": subject, "enabled": True}
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        @api.post("/admin/principals/{subject}/disable")
        async def admin_disable_principal(
            subject: str,
            issuer: str | None = None,
            identity: FluxIdentity = Depends(require_permission("admin:principals:manage")),
        ):
            try:
                from flux.security.principals import PrincipalRegistry

                registry = PrincipalRegistry(session_factory=self._get_db_session)
                principal = registry.find(subject, issuer or "flux")
                if not principal:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Principal '{subject}' not found",
                    )
                registry.set_enabled(principal.id, False)
                return {"status": "success", "subject": subject, "enabled": False}
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        @api.post("/admin/principals/{subject}/keys", status_code=201)
        async def admin_create_principal_key(
            subject: str,
            request: APIKeyRequest,
            issuer: str | None = None,
            identity: FluxIdentity = Depends(require_permission("admin:principals:manage")),
        ):
            try:
                from datetime import timedelta

                from flux.security.principals import PrincipalRegistry

                registry = PrincipalRegistry(session_factory=self._get_db_session)
                principal = registry.find(subject, issuer or "flux")
                if not principal:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Principal '{subject}' not found",
                    )
                if principal.type != "service_account":
                    raise HTTPException(
                        status_code=400,
                        detail="API keys can only be created for service_account principals",
                    )
                expires = (
                    timedelta(days=request.expires_in_days) if request.expires_in_days else None
                )
                key_plaintext = await auth_service.create_api_key(
                    principal.id,
                    request.name,
                    expires,
                )
                return {"key": key_plaintext}
            except HTTPException:
                raise
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e))
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        @api.get("/admin/principals/{subject}/keys")
        async def admin_list_principal_keys(
            subject: str,
            issuer: str | None = None,
            identity: FluxIdentity = Depends(require_permission("admin:principals:read")),
        ):
            try:
                from flux.security.principals import PrincipalRegistry

                registry = PrincipalRegistry(session_factory=self._get_db_session)
                principal = registry.find(subject, issuer or "flux")
                if not principal:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Principal '{subject}' not found",
                    )
                keys = await auth_service.list_api_keys(principal.id)
                return [
                    {
                        "name": k.name,
                        "prefix": k.key_prefix,
                        "expires_at": k.expires_at.isoformat() if k.expires_at else None,
                        "created_at": k.created_at.isoformat(),
                    }
                    for k in keys
                ]
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        @api.delete("/admin/principals/{subject}/keys/{key_name}")
        async def admin_revoke_principal_key(
            subject: str,
            key_name: str,
            issuer: str | None = None,
            identity: FluxIdentity = Depends(require_permission("admin:principals:manage")),
        ):
            try:
                from flux.security.principals import PrincipalRegistry

                registry = PrincipalRegistry(session_factory=self._get_db_session)
                principal = registry.find(subject, issuer or "flux")
                if not principal:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Principal '{subject}' not found",
                    )
                await auth_service.revoke_api_key(principal.id, key_name)
                return {"status": "success", "message": f"Key '{key_name}' revoked"}
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e))
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        # ===========================================
        # Auth: Permissions, Test Token
        # ===========================================

        @api.get("/auth/permissions")
        async def auth_permissions(
            workflow: str | None = None,
            identity: FluxIdentity = Depends(get_identity),
        ):
            try:
                catalog = WorkflowCatalog.create()
                if workflow:
                    from flux.catalogs import resolve_workflow_ref as _resolve_perm

                    _perm_ns, _perm_name = _resolve_perm(workflow)
                    wf = catalog.get(_perm_ns, _perm_name)
                    meta = wf.metadata or {} if hasattr(wf, "metadata") else {}
                    perms = [f"workflow:{wf.namespace}:{wf.name}:read"]
                    perms.extend(
                        auth_service._collect_required_permissions(
                            namespace=wf.namespace,
                            workflow_name=wf.name,
                            workflow_metadata=meta,
                        ),
                    )
                    return perms
                result = {}
                for wf in catalog.all():
                    meta = wf.metadata or {} if hasattr(wf, "metadata") else {}
                    perms = [f"workflow:{wf.namespace}:{wf.name}:read"]
                    perms.extend(
                        auth_service._collect_required_permissions(
                            namespace=wf.namespace,
                            workflow_name=wf.name,
                            workflow_metadata=meta,
                        ),
                    )
                    result[f"{wf.namespace}/{wf.name}"] = perms
                return result
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        @api.post("/auth/test-token")
        @limiter.limit("10/minute")
        async def auth_test_token(
            request: Request,
            body: dict,
            identity: FluxIdentity = Depends(require_permission("admin:*")),
        ):
            try:
                token = body.get("token")
                if not token:
                    return {"valid": False, "error": "Missing 'token' in request body"}
                tested_identity = await auth_service.authenticate(token)
                permissions = await auth_service.resolve_permissions(tested_identity)
                return {
                    "valid": True,
                    "subject": tested_identity.subject,
                    "roles": sorted(tested_identity.roles),
                    "permissions": sorted(permissions),
                }
            except Exception:
                return {"valid": False, "error": "Invalid or expired token"}

        # ===========================================
        # Execution Authorization (task callbacks)
        # ===========================================

        @api.post("/executions/{exec_id}/authorize/{task_name}")
        async def execution_authorize_task(
            exec_id: str,
            task_name: str,
            request: Request,
            identity: FluxIdentity = Depends(get_identity),
        ):
            """Runtime task authorization callback — called by workers before each task.

            Not rate-limited: workers call this on every task execution. The endpoint
            requires a valid execution token bound to this specific exec_id, making
            brute-force impossible without a valid HMAC-signed token.
            """
            try:
                token_type = identity.metadata.get("token_type") if identity.metadata else None
                if token_type != "execution":
                    raise HTTPException(
                        status_code=403,
                        detail="This endpoint requires an execution token",
                    )

                token_exec_id = identity.metadata.get("exec_id")
                if token_exec_id != exec_id:
                    raise HTTPException(
                        status_code=403,
                        detail="Execution token is not bound to this execution",
                    )

                manager = ContextManager.create()
                try:
                    ctx = manager.get(exec_id)
                except Exception:
                    ctx = None
                if ctx is None:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Execution '{exec_id}' not found",
                    )

                from flux.domain import ExecutionState

                terminal_states = {
                    ExecutionState.COMPLETED,
                    ExecutionState.FAILED,
                    ExecutionState.CANCELLED,
                }
                if ctx.state in terminal_states:
                    raise HTTPException(
                        status_code=403,
                        detail=f"Execution is not active (state: {ctx.state.value})",
                    )

                principal_subject = identity.metadata.get("principal_subject") or identity.subject
                principal_issuer = identity.metadata.get("principal_issuer", "flux")

                from flux.security.principals import PrincipalRegistry

                registry = PrincipalRegistry(session_factory=self._get_db_session)
                principal = registry.find(principal_subject, principal_issuer)
                if not principal:
                    raise HTTPException(status_code=403, detail="Principal not found")
                if not principal.enabled:
                    raise HTTPException(status_code=403, detail="Principal is disabled")

                workflow_meta = {}
                try:
                    _auth_ns = ctx.workflow_namespace
                    wf = WorkflowCatalog.create().get(_auth_ns, ctx.workflow_name)
                    workflow_meta = wf.metadata or {} if hasattr(wf, "metadata") else {}
                except Exception:
                    pass

                auth_exempt_tasks = set(workflow_meta.get("auth_exempt_tasks", []))
                if task_name in auth_exempt_tasks:
                    return {"authorized": True}

                if auth_service is not None:
                    roles = registry.get_roles(principal.id)
                    exec_identity = FluxIdentity(
                        subject=principal_subject,
                        roles=frozenset(roles),
                        metadata={"type": principal.type, "issuer": principal_issuer},
                    )
                    required = f"workflow:{_auth_ns}:{ctx.workflow_name}:task:{task_name}:execute"
                    authorized = await auth_service.is_authorized(exec_identity, required)
                    if not authorized:
                        raise HTTPException(
                            status_code=403,
                            detail={"authorized": False, "missing_permission": required},
                        )

                return {"authorized": True}

            except HTTPException:
                raise
            except Exception as e:
                logger.error(
                    f"Execution authorize error for {exec_id}/{task_name}: {e}",
                )
                raise HTTPException(status_code=500, detail=str(e))

        return api


if __name__ == "__main__":  # pragma: no cover
    settings = Configuration.get().settings
    Server(settings.server_host, settings.server_port).start()
