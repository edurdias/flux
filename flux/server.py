from __future__ import annotations

import asyncio
import base64
from typing import Any
from uuid import uuid4

import uvicorn
from fastapi import Body
from fastapi import FastAPI
from fastapi import File
from fastapi import Header
from fastapi import HTTPException
from fastapi import UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sse_starlette import EventSourceResponse

from flux import ExecutionContext
from flux.catalogs import WorkflowCatalog, WorkflowInfo
from flux.config import Configuration
from flux.workflow import workflow
from flux.context_managers import ContextManager
from flux.errors import WorkerNotFoundError, WorkflowNotFoundError
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

        # Scheduler config
        config = Configuration.get().settings.scheduling
        self.poll_interval = config.poll_interval

    def start(self):
        """
        Start Flux server.
        """
        logger.info(f"Starting Flux server at {self.host}:{self.port}")
        logger.debug(f"Server version: {self._get_version()}")

        async def on_server_startup():
            logger.info("Flux server started successfully")
            logger.debug("Server is ready to accept connections")

            # Start integrated scheduler
            await self._start_scheduler()
            logger.info(f"Scheduler started (poll_interval={self.poll_interval}s)")

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

    def _create_execution(self, workflow_name: str, input_data: Any = None) -> ExecutionContext:
        """
        Internal method to create a workflow execution.
        This is used by both the HTTP API and the scheduler.
        """
        workflow = WorkflowCatalog.create().get(workflow_name)
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

        except Exception as e:
            schedule.mark_failure()
            logger.error(f"Failed to trigger scheduled workflow: {str(e)}", exc_info=True)
            raise

    # ===========================================
    # End Scheduler Methods
    # ===========================================

    def _create_api(self) -> FastAPI:
        api = FastAPI(
            title="Flux",
            version=self._get_version(),
            docs_url="/docs",
        )

        # Enable CORS for all origins
        api.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

        backoff_factor = 1.5
        max_delay = 2.0

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
        ):
            try:
                logger.debug(
                    f"Running workflow: {workflow_name} | Mode: {mode} | Detailed: {detailed}",
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
                ctx = self._create_execution(workflow_name, input)
                manager = ContextManager.create()
                logger.debug(
                    f"Created execution context: {ctx.execution_id} for workflow: {workflow_name}",
                )

                if mode == "sync":
                    current_delay = 0.1
                    while not ctx.has_finished:
                        await asyncio.sleep(current_delay)
                        ctx = manager.get(ctx.execution_id)
                        current_delay = min(current_delay * backoff_factor, max_delay)

                if mode == "stream":

                    async def check_for_new_executions():
                        nonlocal ctx
                        while not ctx.has_finished:
                            current_delay = 0.1
                            await asyncio.sleep(current_delay)
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
                                current_delay = 0.1
                            current_delay = min(current_delay * backoff_factor, max_delay)

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

                if mode == "sync":
                    current_delay = 0.1
                    while not ctx.has_finished:
                        await asyncio.sleep(current_delay)
                        ctx = manager.get(ctx.execution_id)
                        current_delay = min(current_delay * backoff_factor, max_delay)

                if mode == "stream":

                    async def check_for_new_executions():
                        nonlocal ctx
                        while not ctx.has_finished:
                            current_delay = 0.1
                            await asyncio.sleep(current_delay)
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
                                current_delay = 0.1
                            current_delay = min(current_delay * backoff_factor, max_delay)

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

                if mode == "sync":
                    current_delay = 0.1
                    while not ctx.has_finished:
                        logger.debug(
                            f"Waiting for cancellation of {execution_id}, current state: {ctx.state.value}",
                        )
                        await asyncio.sleep(current_delay)
                        ctx = manager.get(ctx.execution_id)
                        current_delay = min(current_delay * backoff_factor, max_delay)

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
                logger.info(f"Worker registered successfully: {registration.name}")
                logger.debug(
                    f"Worker details: OS: {registration.runtime.os_name} {registration.runtime.os_version}, "
                    f"Python: {registration.runtime.python_version}, "
                    f"Resources: CPU: {registration.resources.cpu_total}, "
                    f"Memory: {registration.resources.memory_total}",
                )
                return result
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(
                    status_code=400,
                    detail=str(e),
                )

        @api.get("/workers/{name}/connect")
        async def workers_connect(name: str, authorization: str = Header(None)):
            try:
                logger.debug(f"Worker connection request: {name}")
                worker = self._get_worker(name, authorization)
                logger.info(f"Worker connected: {name}")

                async def check_for_new_executions():
                    current_delay = 0.1
                    backoff_factor = 1
                    max_delay = 5

                    context_manager = ContextManager.create()
                    while True:
                        try:
                            ctx = context_manager.next_execution(worker)
                            if ctx:
                                workflow = WorkflowCatalog.create().get(ctx.workflow_name)
                                workflow.source = base64.b64encode(workflow.source).decode("utf-8")

                                logger.debug(
                                    f"Sending execution to worker {name}: {ctx.execution_id} (workflow: {ctx.workflow_name})",
                                )
                                yield {
                                    "id": f"{ctx.execution_id}_{uuid4().hex}",
                                    "event": "execution_scheduled",
                                    "data": to_json({"workflow": workflow, "context": ctx}),
                                }
                                logger.debug(
                                    f"Execution {ctx.execution_id} scheduled for worker {name}",
                                )
                                current_delay = 1

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

                                current_delay = 1

                            ctx = context_manager.next_resume(worker)
                            if ctx:
                                workflow = WorkflowCatalog.create().get(ctx.workflow_name)
                                workflow.source = base64.b64encode(workflow.source).decode("utf-8")
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

                                current_delay = 1

                            await asyncio.sleep(current_delay)
                            current_delay = min(current_delay * backoff_factor, max_delay)
                        except Exception as e:
                            logger.error(f"Error in worker connection stream for {name}: {str(e)}")
                            yield {
                                "event": "error",
                                "data": str(e),
                            }

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
                context_manager = ContextManager.create()
                ctx = context_manager.get(execution_id)
                if not ctx:
                    logger.warning(f"Execution context not found: {execution_id}")
                    raise HTTPException(status_code=404, detail="Execution context not found.")

                # Use Pydantic model for automatic datetime conversion
                domain_ctx = context.to_domain()

                ctx = context_manager.save(domain_ctx)
                logger.debug(f"Checkpoint saved for {execution_id}, state: {ctx.state.value}")
                return ctx.summary()
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
            """Get a specific schedule by ID"""
            try:
                logger.debug(f"Getting schedule '{schedule_id}'")

                schedule_manager = create_schedule_manager()
                schedule = schedule_manager.get_schedule(schedule_id)
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
            """Update an existing schedule"""
            try:
                logger.info(f"Updating schedule '{schedule_id}'")

                schedule_manager = create_schedule_manager()

                # Build update parameters
                schedule_param = None
                if request.schedule_config is not None:
                    schedule_param = schedule_factory(request.schedule_config)

                schedule = schedule_manager.update_schedule(
                    schedule_id,
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
            """Pause a schedule"""
            try:
                logger.info(f"Pausing schedule '{schedule_id}'")

                schedule_manager = create_schedule_manager()
                schedule = schedule_manager.pause_schedule(schedule_id)

                logger.info(f"Successfully paused schedule '{schedule_id}'")
                return _schedule_model_to_response(schedule)

            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error pausing schedule: {str(e)}")
                raise HTTPException(status_code=500, detail=f"Error pausing schedule: {str(e)}")

        @api.post("/schedules/{schedule_id}/resume", response_model=ScheduleResponse)
        async def resume_schedule(schedule_id: str):
            """Resume a paused schedule"""
            try:
                logger.info(f"Resuming schedule '{schedule_id}'")

                schedule_manager = create_schedule_manager()
                schedule = schedule_manager.resume_schedule(schedule_id)

                logger.info(f"Successfully resumed schedule '{schedule_id}'")
                return _schedule_model_to_response(schedule)

            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error resuming schedule: {str(e)}")
                raise HTTPException(status_code=500, detail=f"Error resuming schedule: {str(e)}")

        @api.delete("/schedules/{schedule_id}")
        async def delete_schedule(schedule_id: str):
            """Delete a schedule"""
            try:
                logger.info(f"Deleting schedule '{schedule_id}'")

                schedule_manager = create_schedule_manager()
                success = schedule_manager.delete_schedule(schedule_id)

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

        return api


if __name__ == "__main__":  # pragma: no cover
    settings = Configuration.get().settings
    Server(settings.server_host, settings.server_port).start()
