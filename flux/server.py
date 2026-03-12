from __future__ import annotations

import asyncio
import base64
import time
from typing import Any, Literal
from uuid import uuid4

import uvicorn
from fastapi import Body
from fastapi import FastAPI
from fastapi import File
from fastapi import Header
from fastapi import HTTPException
from fastapi import Query
from fastapi import UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sse_starlette import EventSourceResponse

from flux import ExecutionContext
from flux.catalogs import WorkflowCatalog, WorkflowInfo
from flux.config import Configuration
from flux.workflow import workflow
from flux.context_managers import ContextManager
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
from datetime import datetime, timezone

logger = get_logger(__name__)


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


class SecretRequest(BaseModel):
    """Model for secret creation/update requests"""

    name: str
    value: Any


class SecretResponse(BaseModel):
    """Model for secret responses"""

    name: str
    value: Any | None = None


class ScheduleRequest(BaseModel):
    """Model for schedule creation/update requests"""

    workflow_name: str
    name: str
    schedule_config: dict  # Schedule configuration (cron expression, interval, etc.)
    description: str | None = None
    input_data: Any | None = None


class ScheduleResponse(BaseModel):
    """Model for schedule responses"""

    id: str
    workflow_id: str
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


class ScheduleUpdateRequest(BaseModel):
    """Model for schedule update requests"""

    schedule_config: dict | None = None
    description: str | None = None
    input_data: Any | None = None


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
    packages: list[dict[str, str]] = []


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
        self._worker_last_pong: dict[str, float] = {}
        self._worker_cache: dict[str, WorkerResponse] = {}
        self._worker_offline_since: dict[str, float] = {}
        self._worker_evicted: dict[str, asyncio.Event] = {}

        config = Configuration.get().settings.scheduling
        self.poll_interval = config.poll_interval

        workers_config = Configuration.get().settings.workers
        self.heartbeat_interval = workers_config.heartbeat_interval
        self.heartbeat_timeout = workers_config.heartbeat_timeout
        self.offline_ttl = workers_config.offline_ttl

        try:
            from flux.observability import setup as setup_observability

            obs_config = Configuration.get().settings.observability
            setup_observability(obs_config)
        except Exception:
            logger.debug("Observability setup skipped or failed")

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
                    if isinstance(obj, workflow) and obj.name == workflow_info.name:
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
        workflow_name: str,
        input_data: Any = None,
        version: int | None = None,
    ) -> ExecutionContext:
        """
        Internal method to create a workflow execution.
        This is used by both the HTTP API and the scheduler.

        Args:
            workflow_name: Name of the workflow to execute
            input_data: Optional input data for the workflow
            version: Optional specific version to execute (defaults to latest)
        """
        workflow = WorkflowCatalog.create().get(workflow_name, version)
        if not workflow:
            raise WorkflowNotFoundError(f"Workflow '{workflow_name}' not found")

        ctx = ContextManager.create().save(
            ExecutionContext(
                workflow_id=workflow.id,
                workflow_name=workflow.name,
                input=input_data,
                requests=workflow.requests,
            ),
        )

        from flux.observability import get_metrics

        m = get_metrics()
        if m:
            m.record_execution_started(workflow_name)
            m.record_execution_queued()

        return ctx

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

    def _disconnect_worker(self, name: str) -> None:
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
            m.record_worker_disconnected(name, "disconnect")

    async def _run_heartbeat_reaper(self):
        """Background task that evicts stale workers and prunes offline cache."""
        try:
            while True:
                await asyncio.sleep(self.heartbeat_interval)
                now = time.monotonic()

                stale = [
                    name
                    for name, last_pong in self._worker_last_pong.items()
                    if (now - last_pong) > self.heartbeat_timeout
                ]
                for name in stale:
                    logger.warning(f"Worker {name} missed heartbeat, evicting")
                    self._disconnect_worker(name)
                    logger.info(f"Worker {name} evicted (remaining: {len(self._worker_names)})")

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
                            self._trigger_scheduled_workflow(schedule, current_time)
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

    def _trigger_scheduled_workflow(self, schedule, scheduled_time: datetime):
        """
        Trigger a scheduled workflow execution.
        Simple trigger-and-forget pattern - creates execution and lets workers handle it.
        """
        logger.info(
            f"Triggering scheduled workflow '{schedule.workflow_name}' (schedule: {schedule.name})",
        )

        try:
            # Use the common execution creation method
            ctx = self._create_execution(schedule.workflow_name, schedule.input_data)

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

        api.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

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
        async def workflows_save(file: UploadFile = File(...)):
            source = await file.read()
            logger.info(f"Received file: {file.filename} with size: {len(source)} bytes:")
            try:
                logger.debug(f"Processing workflow file: {file.filename}")
                catalog = WorkflowCatalog.create()
                workflows = catalog.parse(source)
                result = catalog.save(workflows)
                logger.debug(f"Saved workflows: {[w.name for w in workflows]}")

                self._auto_create_schedules_from_source(source, workflows)

                return result
            except SyntaxError as e:
                logger.error(f"Syntax error while saving workflow: {str(e)}")
                raise HTTPException(status_code=400, detail=str(e))
            except Exception as e:
                logger.error(f"Error saving workflow: {str(e)}")
                raise HTTPException(status_code=500, detail=f"Error saving workflow: {str(e)}")

        @api.get("/workflows")
        async def workflows_all():
            try:
                logger.debug("Fetching all workflows")
                catalog = WorkflowCatalog.create()
                workflows = catalog.all()
                result = [{"name": w.name, "version": w.version} for w in workflows]
                logger.debug(f"Found {len(result)} workflows")
                return result
            except Exception as e:
                logger.error(f"Error listing workflows: {str(e)}")
                raise HTTPException(status_code=500, detail=f"Error listing workflows: {str(e)}")

        @api.get("/workflows/{workflow_name}")
        async def workflows_get(workflow_name: str):
            try:
                logger.debug(f"Fetching workflow: {workflow_name}")
                catalog = WorkflowCatalog.create()
                workflow = catalog.get(workflow_name)
                logger.debug(f"Found workflow: {workflow_name} (version: {workflow.version})")
                return workflow.to_dict()
            except WorkflowNotFoundError as e:
                raise HTTPException(status_code=404, detail=str(e))
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Error retrieving workflow: {str(e)}")

        @api.post("/workflows/{workflow_name}/run/{mode}")
        async def workflows_run(
            workflow_name: str,
            input: Any = Body(None),
            mode: str = "async",
            detailed: bool = False,
            version: int | None = None,
        ):
            try:
                logger.debug(
                    f"Running workflow: {workflow_name} (version: {version or 'latest'}) "
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

                # Use internal method to create execution
                ctx = self._create_execution(workflow_name, input, version)
                manager = ContextManager.create()
                logger.debug(
                    f"Created execution context: {ctx.execution_id} for workflow: {workflow_name}",
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

                    async def check_for_new_executions():
                        nonlocal ctx
                        event = self._execution_events[ctx.execution_id]
                        try:
                            while not ctx.has_finished:
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

                    return EventSourceResponse(
                        check_for_new_executions(),
                        media_type="text/event-stream",
                        headers={
                            "Content-Type": "text/event-stream",
                            "Cache-Control": "no-cache",
                            "Connection": "keep-alive",
                        },
                    )

                dto = ExecutionContextDTO.from_domain(ctx)
                result = dto.summary() if not detailed else dto
                logger.debug(
                    f"Returning execution result for {ctx.execution_id} in state: {ctx.state.value}",
                )
                return result

            except WorkflowNotFoundError as e:
                logger.error(f"Workflow not found: {str(e)}")
                raise HTTPException(status_code=404, detail=str(e))
            except Exception as e:
                logger.error(f"Error scheduling workflow {workflow_name}: {str(e)}")
                raise HTTPException(status_code=500, detail=f"Error scheduling workflow: {str(e)}")

        @api.post("/workflows/{workflow_name}/resume/{execution_id}/{mode}")
        async def workflows_resume(
            workflow_name: str,
            execution_id: str,
            input: Any = Body(None),
            mode: str = "async",
            detailed: bool = False,
        ):
            try:
                logger.debug(
                    f"Resuming workflow: {workflow_name} | Execution ID: {execution_id} | Mode: {mode} | Detailed: {detailed}",
                )
                logger.debug(f"Input: {to_json(input)}")

                if not workflow_name:
                    raise HTTPException(status_code=400, detail="Workflow name is required.")

                if not execution_id:
                    raise HTTPException(status_code=400, detail="Execution ID is required.")

                if mode not in ["sync", "async", "stream"]:
                    raise HTTPException(
                        status_code=400,
                        detail="Invalid mode. Use 'sync', 'async', or 'stream'.",
                    )

                manager = ContextManager.create()

                ctx = manager.get(execution_id)

                if ctx is None:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Execution context with ID {execution_id} not found.",
                    )

                if ctx.has_finished:
                    raise HTTPException(
                        status_code=400,
                        detail="Cannot resume a finished execution.",
                    )

                ctx.start_resuming(input)
                manager.save(ctx)
                logger.debug(
                    f"Resuming execution context: {ctx.execution_id} for workflow: {workflow_name}",
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

                    async def check_for_new_executions():
                        nonlocal ctx
                        event = self._execution_events[ctx.execution_id]
                        try:
                            while not ctx.has_finished:
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

                    return EventSourceResponse(
                        check_for_new_executions(),
                        media_type="text/event-stream",
                        headers={
                            "Content-Type": "text/event-stream",
                            "Cache-Control": "no-cache",
                            "Connection": "keep-alive",
                        },
                    )

                dto = ExecutionContextDTO.from_domain(ctx)
                result = dto.summary() if not detailed else dto
                logger.debug(
                    f"Returning execution result for {ctx.execution_id} in state: {ctx.state.value}",
                )
                return result

            except WorkflowNotFoundError as e:
                logger.error(f"Workflow not found: {str(e)}")
                raise HTTPException(status_code=404, detail=str(e))
            except Exception as e:
                logger.error(f"Error scheduling workflow {workflow_name}: {str(e)}")
                raise HTTPException(status_code=500, detail=f"Error scheduling workflow: {str(e)}")

        @api.get("/workflows/{workflow_name}/status/{execution_id}")
        async def workflows_status(workflow_name: str, execution_id: str, detailed: bool = False):
            try:
                logger.debug(
                    f"Checking status for workflow: {workflow_name} | Execution ID: {execution_id}",
                )
                manager = ContextManager.create()
                context = manager.get(execution_id)
                dto = ExecutionContextDTO.from_domain(context)
                result = dto.summary() if not detailed else dto
                logger.debug(f"Status for {execution_id}: {context.state.value}")
                return result
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Error inspecting workflow: {str(e)}")

        @api.get("/workflows/{workflow_name}/cancel/{execution_id}")
        async def workflows_cancel(
            workflow_name: str,
            execution_id: str,
            mode: str = "async",
            detailed: bool = False,
        ):
            try:
                logger.debug(
                    f"Cancelling workflow: {workflow_name} | Execution ID: {execution_id} | Mode: {mode}",
                )

                if not workflow_name:
                    raise HTTPException(status_code=400, detail="Workflow name is required.")

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
                logger.info(f"Workflow {workflow_name} execution {execution_id} is {dto.state}.")
                return result
            except WorkflowNotFoundError as e:
                logger.error(f"Workflow not found: {str(e)}")
                raise HTTPException(status_code=404, detail=str(e))
            except WorkerNotFoundError as e:
                logger.error(f"Worker not found: {str(e)}")
                raise HTTPException(status_code=404, detail=str(e))
            except HTTPException as he:
                logger.error(f"HTTP error while cancelling workflow {workflow_name}: {str(he)}")
                raise
            except Exception as e:
                logger.error(f"Error cancelling workflow {workflow_name}: {str(e)}")
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
                    m.record_worker_connected(registration.name)

                return result
            except HTTPException:
                raise
            except Exception as e:
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
                self._worker_offline_since.pop(name, None)
                if name in self._worker_cache:
                    self._worker_cache[name].status = "online"
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
                                    workflow = WorkflowCatalog.create().get(ctx.workflow_name)
                                    workflow.source = base64.b64encode(workflow.source).decode(
                                        "utf-8",
                                    )

                                    logger.debug(
                                        f"Sending execution to worker {name}: {ctx.execution_id} (workflow: {ctx.workflow_name})",
                                    )

                                    import json as _json

                                    from flux.observability.tracing import inject_trace_context

                                    data_payload = to_json(
                                        {"workflow": workflow, "context": ctx},
                                    )
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
                                    workflow = WorkflowCatalog.create().get(ctx.workflow_name)
                                    workflow.source = base64.b64encode(workflow.source).decode(
                                        "utf-8",
                                    )
                                    logger.debug(
                                        f"Sending resume to worker {name}: {ctx.execution_id} (workflow: {ctx.workflow_name})",
                                    )

                                    yield {
                                        "id": f"{ctx.execution_id}_{uuid4().hex}",
                                        "event": "execution_resumed",
                                        "data": to_json({"workflow": workflow, "context": ctx}),
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
                        self._disconnect_worker(name)
                        logger.info(
                            f"Worker {name} disconnected (remaining: {len(self._worker_names)})",
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
            try:
                logger.debug(f"Worker {name} claiming execution: {execution_id}")
                worker = self._get_worker(name, authorization)
                context_manager = ContextManager.create()
                ctx = context_manager.claim(execution_id, worker)
                logger.info(f"Execution {execution_id} claimed by worker {name}")

                from flux.observability import get_metrics

                m = get_metrics()
                if m:
                    m.record_execution_claimed()

                # Notify any waiting sync/stream endpoint
                event = self._execution_events.get(execution_id)
                if event:
                    event.set()

                return ctx.summary()
            except Exception as e:
                logger.error(f"Error claiming execution {execution_id} by worker {name}: {str(e)}")
                raise HTTPException(status_code=404, detail=str(e))

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

        # Admin API - Secrets Management
        @api.get("/admin/secrets")
        async def admin_list_secrets():
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
        async def admin_get_secret(name: str):
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
        async def admin_delete_secret(name: str):
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

        # Scheduling API
        def _schedule_model_to_response(schedule) -> ScheduleResponse:
            """Convert ScheduleModel to ScheduleResponse"""
            return ScheduleResponse(
                id=schedule.id,
                workflow_id=schedule.workflow_id,
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
        async def create_schedule(request: ScheduleRequest):
            """Create a new schedule for a workflow"""
            try:
                logger.info(
                    f"Creating schedule '{request.name}' for workflow '{request.workflow_name}'",
                )

                # Get workflow from catalog to ensure it exists
                catalog = WorkflowCatalog.create()
                workflow_def = catalog.get(request.workflow_name)
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
                    workflow_name=request.workflow_name,
                    name=request.name,
                    schedule=schedule,
                    description=request.description,
                    input_data=request.input_data,
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
        ):
            """List all schedules, optionally filtered by workflow with pagination support"""
            try:
                logger.debug(
                    f"Listing schedules (workflow: {workflow_name}, active_only: {active_only}, "
                    f"limit: {limit}, offset: {offset})",
                )

                schedule_manager = create_schedule_manager()

                if workflow_name:
                    # Get workflow to get its ID
                    catalog = WorkflowCatalog.create()
                    workflow_def = catalog.get(workflow_name)
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

                result = [_schedule_model_to_response(s) for s in schedules]
                logger.debug(f"Found {len(result)} schedules")
                return result

            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error listing schedules: {str(e)}")
                raise HTTPException(status_code=500, detail=f"Error listing schedules: {str(e)}")

        @api.get("/schedules/{schedule_id}", response_model=ScheduleResponse)
        async def get_schedule(schedule_id: str):
            """Get a specific schedule by ID or name"""
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

                return _schedule_model_to_response(schedule)

            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error getting schedule: {str(e)}")
                raise HTTPException(status_code=500, detail=f"Error getting schedule: {str(e)}")

        @api.put("/schedules/{schedule_id}", response_model=ScheduleResponse)
        async def update_schedule(schedule_id: str, request: ScheduleUpdateRequest):
            """Update an existing schedule (accepts either schedule ID or name)"""
            try:
                logger.info(f"Updating schedule '{schedule_id}'")

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
                )

                logger.info(f"Successfully updated schedule '{schedule_id}'")
                return _schedule_model_to_response(schedule)

            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error updating schedule: {str(e)}")
                raise HTTPException(status_code=500, detail=f"Error updating schedule: {str(e)}")

        @api.post("/schedules/{schedule_id}/pause", response_model=ScheduleResponse)
        async def pause_schedule(schedule_id: str):
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
        async def resume_schedule(schedule_id: str):
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
        async def delete_schedule(schedule_id: str):
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

        @api.delete("/workflows/{workflow_name}")
        async def workflow_delete(workflow_name: str, version: int | None = None):
            """Delete workflow by name, optionally specific version."""
            try:
                logger.info(
                    f"Deleting workflow '{workflow_name}'"
                    + (f" version {version}" if version else " (all versions)"),
                )

                catalog = WorkflowCatalog.create()

                # Check if workflow exists
                try:
                    catalog.get(workflow_name, version)
                except WorkflowNotFoundError:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Workflow '{workflow_name}'"
                        + (f" version {version}" if version else "")
                        + " not found",
                    )

                catalog.delete(workflow_name, version)

                logger.info(f"Successfully deleted workflow '{workflow_name}'")
                return {
                    "status": "success",
                    "message": f"Workflow '{workflow_name}'"
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
            "/workflows/{workflow_name}/versions",
            response_model=list[WorkflowVersionResponse],
        )
        async def workflow_versions(workflow_name: str):
            """List all versions of a workflow."""
            try:
                logger.debug(f"Fetching versions for workflow: {workflow_name}")

                catalog = WorkflowCatalog.create()
                versions = catalog.versions(workflow_name)

                if not versions:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Workflow '{workflow_name}' not found",
                    )

                result = [
                    WorkflowVersionResponse(
                        id=v.id,
                        name=v.name,
                        version=v.version,
                    )
                    for v in versions
                ]
                logger.debug(f"Found {len(result)} versions for workflow '{workflow_name}'")
                return result

            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error listing workflow versions: {str(e)}")
                raise HTTPException(
                    status_code=500,
                    detail=f"Error listing workflow versions: {str(e)}",
                )

        @api.get("/workflows/{workflow_name}/versions/{version}")
        async def workflow_version_get(workflow_name: str, version: int):
            """Get specific workflow version."""
            try:
                logger.debug(f"Fetching workflow '{workflow_name}' version {version}")

                catalog = WorkflowCatalog.create()
                workflow = catalog.get(workflow_name, version)

                logger.debug(f"Found workflow '{workflow_name}' version {version}")
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
            state: str | None = None,
            limit: int = 50,
            offset: int = 0,
        ):
            """List executions with optional filtering."""
            try:
                logger.debug(
                    f"Listing executions (workflow: {workflow_name}, state: {state}, "
                    f"limit: {limit}, offset: {offset})",
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
                    state=state_filter,
                    limit=limit,
                    offset=offset,
                )

                result = ExecutionListResponse(
                    executions=[
                        ExecutionSummaryResponse(
                            execution_id=ex.execution_id,
                            workflow_id=ex.workflow_id,
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
        async def execution_get(execution_id: str, detailed: bool = False):
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
            "/workflows/{workflow_name}/executions",
            response_model=ExecutionListResponse,
        )
        async def workflow_executions_list(
            workflow_name: str,
            state: str | None = None,
            limit: int = 50,
            offset: int = 0,
        ):
            """List executions for a specific workflow."""
            try:
                logger.debug(
                    f"Listing executions for workflow '{workflow_name}' "
                    f"(state: {state}, limit: {limit}, offset: {offset})",
                )

                from flux.domain import ExecutionState

                # Check workflow exists
                catalog = WorkflowCatalog.create()
                try:
                    catalog.get(workflow_name)
                except WorkflowNotFoundError:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Workflow '{workflow_name}' not found",
                    )

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
                    state=state_filter,
                    limit=limit,
                    offset=offset,
                )

                result = ExecutionListResponse(
                    executions=[
                        ExecutionSummaryResponse(
                            execution_id=ex.execution_id,
                            workflow_id=ex.workflow_id,
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

                logger.debug(f"Found {total} executions for workflow '{workflow_name}'")
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
        async def workers_list(status: Literal["online", "offline"] | None = Query(None)):
            """List workers from in-memory cache. Optional ?status=online|offline filter."""
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
        async def worker_get(name: str):
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

        return api


if __name__ == "__main__":  # pragma: no cover
    settings = Configuration.get().settings
    Server(settings.server_host, settings.server_port).start()
