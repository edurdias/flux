from __future__ import annotations

import asyncio
import base64
import importlib
import platform
import random
import sys
import time
from collections.abc import Awaitable
from types import ModuleType
from collections.abc import Callable

import httpx
import psutil
from httpx_sse import aconnect_sse
from pydantic import BaseModel

from flux import ExecutionContext
from flux.config import Configuration
from flux.domain.events import ExecutionEvent
from flux.errors import WorkflowNotFoundError
from flux.utils import get_logger
from flux import workflow

logger = get_logger(__name__)


def _make_module_cache_key(namespace: str, name: str, version: int) -> str:
    return f"{namespace}:{name}:{version}"


def _make_module_name(namespace: str, name: str, version: int) -> str:
    safe_namespace = namespace.replace("-", "_")
    safe_name = name.replace("-", "_")
    return f"flux_workflow__{safe_namespace}__{safe_name}__v{version}"


class WorkflowDefinition(BaseModel):
    id: str
    namespace: str = "default"
    name: str
    version: int
    source: str


class WorkflowExecutionRequest(BaseModel):
    workflow: WorkflowDefinition
    context: ExecutionContext
    exec_token: str | None = None

    class Config:
        arbitrary_types_allowed = True

    @staticmethod
    def from_json(
        data: dict,
        checkpoint: Callable[[ExecutionContext], Awaitable],
    ) -> WorkflowExecutionRequest:
        exec_token = data.get("exec_token")
        ctx: ExecutionContext = ExecutionContext(
            workflow_id=data["context"]["workflow_id"],
            workflow_namespace=data["context"].get("workflow_namespace", "default"),
            workflow_name=data["context"]["workflow_name"],
            input=data["context"]["input"],
            execution_id=data["context"]["execution_id"],
            state=data["context"]["state"],
            events=[ExecutionEvent(**event) for event in data["context"]["events"]],
            checkpoint=checkpoint,
        )
        if exec_token:
            ctx.set_exec_token(exec_token)
        return WorkflowExecutionRequest(
            workflow=WorkflowDefinition(**data["workflow"]),
            context=ctx,
            exec_token=exec_token,
        )


class Worker:
    def __init__(self, name: str, server_url: str, labels: dict[str, str] | None = None):
        self.name = name
        self.labels = labels or {}
        config = Configuration.get().settings.workers
        # Normalize so a whitespace-only token (e.g. an empty-after-strip env var)
        # surfaces as "not configured" instead of being sent as `Bearer    ` and
        # producing a confusing 403 from the server.
        bootstrap_token = (
            config.bootstrap_token.strip() if config.bootstrap_token is not None else None
        )
        if not bootstrap_token:
            raise RuntimeError(
                "Worker bootstrap token is not configured. Set FLUX_WORKERS__BOOTSTRAP_TOKEN "
                "or 'bootstrap_token' under [flux.workers] in flux.toml. Retrieve the server's "
                "token by running 'flux server bootstrap-token' on the server host.",
            )
        self.bootstrap_token = bootstrap_token
        self.base_url = f"{server_url or config.server_url}/workers"
        self.client = httpx.AsyncClient(timeout=config.default_timeout or None)
        self._running_workflows: dict[str, asyncio.Task] = {}
        self._pending_checkpoints: dict[str, asyncio.Task] = {}
        self._progress_queues: dict[str, asyncio.Queue] = {}
        self._progress_flushers: dict[str, asyncio.Task | None] = {}
        self._reconnect_max_delay = config.reconnect_max_delay
        self._module_cache: dict[str, tuple[ModuleType, float]] = {}
        self._module_cache_ttl = config.module_cache_ttl
        self._registered = False
        self.session_token: str | None = None

    def start(self):
        logger.info("Worker starting up...")
        logger.debug(f"Worker name: {self.name}")
        logger.debug(f"Server URL: {self.base_url}")

        obs_config = Configuration.get().settings.observability
        if obs_config.enabled:
            from flux.observability import setup as setup_observability

            setup_observability(obs_config)
            logger.info("Worker observability initialized")

        try:
            asyncio.run(self._run())
        except KeyboardInterrupt:
            logger.info("Worker interrupted by user")
        finally:
            try:
                from flux.observability import shutdown as shutdown_observability

                shutdown_observability()
            except Exception:
                pass
            logger.info("Worker shutting down...")

    async def _run(self):
        """Main worker loop: register, connect, reconnect on failure.

        On first run, performs full registration (gathers runtime, resources, packages).
        On reconnect, skips registration and reuses the existing session token.
        If the server rejects the token (401/403), falls back to full re-registration.
        """
        backoff = 1
        while True:
            try:
                if not self._registered:
                    await self._register()
                else:
                    logger.info("Reconnecting with existing session...")

                try:
                    await self._connect()
                except httpx.HTTPStatusError as e:
                    if e.response.status_code in (401, 403) and self._registered:
                        logger.warning("Session token rejected, re-registering...")
                        self._registered = False
                        await self._register()
                        await self._connect()
                    else:
                        raise

                logger.info("SSE connection closed, reconnecting...")
                backoff = 1
            except KeyboardInterrupt:
                raise
            except Exception as e:
                jitter = backoff * (0.5 + random.random())
                delay = min(jitter, self._reconnect_max_delay)
                logger.warning(
                    f"Connection lost ({type(e).__name__}: {e}). Reconnecting in {delay:.1f}s...",
                )
                await asyncio.sleep(delay)
                backoff = min(backoff * 2, self._reconnect_max_delay)

    async def _register(self):
        try:
            logger.info(f"Registering worker '{self.name}' with server...")
            logger.debug(f"Registration endpoint: {self.base_url}/register")

            runtime = await self._get_runtime_info()
            resources = await self._get_resources_info()
            packages = await self._get_installed_packages()

            logger.debug(f"Runtime info: {runtime}")
            logger.debug(
                f"Resource info: CPU: {resources['cpu_total']}, Memory: {resources['memory_total']}, Disk: {resources['disk_total']}",
            )
            logger.debug(f"Number of packages to register: {len(packages)}")

            registration = {
                "name": self.name,
                "runtime": runtime,
                "resources": resources,
                "packages": packages,
                "labels": self.labels,
            }

            logger.debug("Sending registration request to server...")
            response = await self.client.post(
                f"{self.base_url}/register",
                json=registration,
                headers={"Authorization": f"Bearer {self.bootstrap_token}"},
            )
            response.raise_for_status()
            data = response.json()
            self.session_token = data["session_token"]
            self._registered = True
            logger.debug("Registration successful, received session token")
            logger.info("OK")
        except Exception as e:
            logger.error("ERROR")
            logger.exception(e)
            raise

    async def _connect(self):
        """Connect to SSE endpoint and handle events asynchronously"""
        logger.info("Establishing connection with server...")

        base_url = f"{self.base_url}/{self.name}"
        headers = {"Authorization": f"Bearer {self.session_token}"}

        logger.debug(f"SSE connection URL: {base_url}/connect")
        logger.debug("Setting up HTTP client for long-running connection")

        try:
            async with httpx.AsyncClient(timeout=None) as client:
                logger.debug("Initiating SSE connection...")
                async with aconnect_sse(
                    client,
                    "GET",
                    f"{base_url}/connect",
                    headers=headers,
                ) as es:
                    logger.info("Connection established successfully")
                    logger.debug("Starting event loop to receive events")
                    async for evt in es.aiter_sse():
                        if evt.event == "execution_scheduled":
                            asyncio.create_task(
                                self._handle_execution_scheduled(base_url, headers, evt),
                                name=f"handle_execution_scheduled_{evt.id}",
                            )
                        elif evt.event == "execution_cancelled":
                            asyncio.create_task(
                                self._handle_execution_cancelled(evt),
                                name=f"handle_execution_cancelled_{evt.id}",
                            )
                        elif evt.event == "execution_resumed":
                            asyncio.create_task(
                                self._handle_execution_resumed(evt),
                                name=f"handle_execution_resumed_{evt.id}",
                            )
                        elif evt.event == "ping":
                            asyncio.create_task(self._send_pong())
                        elif evt.event == "keep-alive":
                            logger.debug("Event received: Keep-alive")
                        elif evt.event == "error":
                            logger.error(f"Event received: Error - {evt.data}")

        except Exception as evt:
            logger.error(f"Error in SSE connection: {str(evt)}")
            logger.debug(f"Connection error details: {type(evt).__name__}: {str(evt)}")
            raise

    async def _send_pong(self):
        """Respond to server ping with a pong."""
        base_url = f"{self.base_url}/{self.name}"
        headers = {"Authorization": f"Bearer {self.session_token}"}
        try:
            await self.client.post(f"{base_url}/pong", headers=headers)
            logger.debug("Pong sent")
        except Exception as e:
            logger.debug(f"Failed to send pong: {e}")

    async def _handle_execution_cancelled(self, e):
        """Handle execution cancelled event asynchronously.

        This method is called as a separate task and should handle its own exceptions.
        """
        try:
            logger.debug("Received execution_cancelled event")
            data = e.json()
            context = ExecutionContext.from_json(data["context"], self._checkpoint)
            logger.info(f"Cancelling Execution - {context.workflow_name} - {context.execution_id}")
            if task := self._running_workflows.get(context.execution_id):
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    logger.info(f"Execution {context.execution_id} cancelled successfully")
                    from flux.observability import get_metrics

                    m = get_metrics()
                    if m:
                        m.record_workflow_completed(
                            context.workflow_namespace,
                            context.workflow_name,
                            "cancelled",
                            0,
                        )
                finally:
                    self._running_workflows.pop(context.execution_id, None)
        except Exception as ex:
            logger.error(f"Error handling execution_cancelled event: {str(ex)}")
            logger.debug(f"Exception details: {type(ex).__name__}: {str(ex)}", exc_info=True)

    async def _handle_execution_resumed(self, e):
        """Handle execution resumed event asynchronously."""
        import contextlib

        from flux.observability import get_metrics, is_enabled

        event_data = e.json()
        request = WorkflowExecutionRequest.from_json(event_data, self._checkpoint)
        m = get_metrics()

        span_cm = contextlib.nullcontext()
        if is_enabled():
            from opentelemetry import trace as _trace

            from flux.observability.tracing import extract_trace_context

            trace_ctx = event_data.get("trace_context", {})
            parent_context = extract_trace_context(trace_ctx) if trace_ctx else None

            tracer = _trace.get_tracer("flux")
            span_cm = tracer.start_as_current_span(
                "flux.workflow.resume",
                context=parent_context,
                attributes={
                    "flux.workflow.name": request.workflow.name,
                    "flux.execution.id": request.context.execution_id,
                    "flux.worker.name": self.name,
                },
            )

        try:
            with span_cm as span:
                logger.info(
                    f"Resuming Execution - {request.workflow.name} v{request.workflow.version} - {request.context.execution_id}",
                )

                base_url = f"{self.base_url}/{self.name}"
                headers = {"Authorization": f"Bearer {self.session_token}"}

                logger.debug(f"Claiming resumed execution: {request.context.execution_id}")
                try:
                    response = await self.client.post(
                        f"{base_url}/claim/{request.context.execution_id}",
                        headers=headers,
                    )
                    response.raise_for_status()
                except httpx.HTTPStatusError as claim_err:
                    if claim_err.response.status_code == 409:
                        logger.info(
                            f"Resume claim for {request.context.execution_id} returned 409 "
                            f"(already claimed); dropping duplicate dispatch.",
                        )
                        return
                    raise

                logger.info(
                    f"Execution Claimed - {request.workflow.name} v{request.workflow.version} - {request.context.execution_id}",
                )

                claim_data = response.json()
                request.context = ExecutionContext.from_json(claim_data, self._checkpoint)
                if request.exec_token:
                    request.context.set_exec_token(request.exec_token)

                if m:
                    m.record_worker_execution_started(self.name)

                try:
                    ctx = await self._execute_workflow(request)
                finally:
                    if m:
                        m.record_worker_execution_ended(self.name)

                if ctx.has_failed:
                    if span:
                        from opentelemetry.trace import StatusCode

                        span.set_status(StatusCode.ERROR, str(ctx.output))
                    logger.error(
                        f"Execution {ctx.state.value} - {request.workflow.name} v{request.workflow.version} - {request.context.execution_id}",
                    )
                else:
                    logger.info(
                        f"Execution {ctx.state.value} - {request.workflow.name} v{request.workflow.version} - {request.context.execution_id}",
                    )
        except Exception as ex:
            logger.error(f"Error handling execution_resumed event: {str(ex)}")
            logger.debug(f"Exception details: {type(ex).__name__}: {str(ex)}", exc_info=True)

    async def _handle_execution_scheduled(self, base_url, headers, e):
        """Handle execution scheduled event asynchronously.

        This method is called as a separate task and should handle its own exceptions.
        """
        import contextlib

        from flux.observability import get_metrics, is_enabled

        event_data = e.json()
        request = WorkflowExecutionRequest.from_json(event_data, self._checkpoint)
        m = get_metrics()

        span_cm = contextlib.nullcontext()
        if is_enabled():
            from opentelemetry import trace as _trace

            from flux.observability.tracing import extract_trace_context

            trace_ctx = event_data.get("trace_context", {})
            parent_context = extract_trace_context(trace_ctx) if trace_ctx else None

            tracer = _trace.get_tracer("flux")
            span_cm = tracer.start_as_current_span(
                "flux.workflow.execute",
                context=parent_context,
                attributes={
                    "flux.workflow.name": request.workflow.name,
                    "flux.execution.id": request.context.execution_id,
                    "flux.worker.name": self.name,
                },
            )

        try:
            with span_cm as span:
                logger.info(
                    f"Execution Scheduled - {request.workflow.name} v{request.workflow.version} - {request.context.execution_id}",
                )
                logger.debug(f"Workflow input: {request.context.input}")

                logger.debug(f"Claiming execution: {request.context.execution_id}")
                response = await self.client.post(
                    f"{base_url}/claim/{request.context.execution_id}",
                    headers=headers,
                )
                response.raise_for_status()
                claim_data = response.json()
                request.context = ExecutionContext.from_json(claim_data, self._checkpoint)
                if request.exec_token:
                    request.context.set_exec_token(request.exec_token)
                logger.debug(f"Claim response: {claim_data}")

                logger.info(
                    f"Execution Claimed - {request.workflow.name} v{request.workflow.version} - {request.context.execution_id}",
                )

                if m:
                    m.record_worker_execution_started(self.name)

                try:
                    logger.debug(f"Starting workflow execution: {request.workflow.name}")
                    ctx = await self._execute_workflow(request)
                    logger.debug(
                        f"Workflow execution completed with state: {ctx.state.value}",
                    )
                finally:
                    if m:
                        m.record_worker_execution_ended(self.name)

                if ctx.has_failed:
                    if span:
                        from opentelemetry.trace import StatusCode

                        span.set_status(StatusCode.ERROR, str(ctx.output))
                    logger.error(
                        f"Execution {ctx.state.value} - {request.workflow.name} v{request.workflow.version} - {request.context.execution_id}",
                    )
                    logger.debug(
                        f"Failure details: {ctx.events[-1].value if ctx.events else 'No details'}",
                    )
                else:
                    logger.info(
                        f"Execution {ctx.state.value} - {request.workflow.name} v{request.workflow.version} - {request.context.execution_id}",
                    )
                    logger.debug(f"Execution output: {ctx.output}")
        except Exception as ex:
            logger.error(f"Error handling execution_scheduled event: {str(ex)}")
            logger.debug(f"Exception details: {type(ex).__name__}: {str(ex)}", exc_info=True)

    async def _execute_workflow(self, request: WorkflowExecutionRequest) -> ExecutionContext:
        """Execute a workflow from a workflow execution request.

        Args:
            request: The workflow execution request containing the workflow definition and context

        Returns:
            ExecutionContext: The execution context after workflow execution
        """
        from flux.remote_managers import (
            RemoteConfigManager,
            RemoteSecretManager,
            reset_remote_managers,
            set_remote_managers,
        )

        server_url = self.base_url.rsplit("/workers", 1)[0]
        config_manager = RemoteConfigManager(server_url, self.session_token)
        secret_manager = RemoteSecretManager(
            server_url,
            self.session_token,
            worker_name=self.name,
            execution_id=request.context.execution_id,
        )
        remote_tokens = set_remote_managers(
            config=config_manager,
            secret=secret_manager,
        )
        try:
            return await self._run_workflow(request)
        finally:
            reset_remote_managers(remote_tokens)
            await config_manager.aclose()
            await secret_manager.aclose()

    async def _run_workflow(self, request: WorkflowExecutionRequest) -> ExecutionContext:
        logger.debug(
            f"Preparing to execute workflow: {request.workflow.name} v{request.workflow.version}",
        )

        cache_key = _make_module_cache_key(
            request.workflow.namespace,
            request.workflow.name,
            request.workflow.version,
        )
        module = None
        wfunc = None

        from flux.observability import get_metrics

        m = get_metrics()

        module_name = _make_module_name(
            request.workflow.namespace,
            request.workflow.name,
            request.workflow.version,
        )

        if self._module_cache_ttl > 0:
            cached = self._module_cache.get(cache_key)
            if cached:
                cached_module, cached_at = cached
                if time.monotonic() - cached_at < self._module_cache_ttl:
                    module = cached_module
                    logger.debug(f"Module cache hit for {cache_key}")
                    if m:
                        m.record_module_cache("hit")
                else:
                    del self._module_cache[cache_key]
                    sys.modules.pop(module_name, None)

        if module is None:
            if m and self._module_cache_ttl > 0:
                m.record_module_cache("miss")

            source_code = base64.b64decode(request.workflow.source).decode("utf-8")
            logger.debug(f"Decoded workflow source code ({len(source_code)} bytes)")

            # Drop any stale module under this name before exec'ing the new
            # source. Otherwise a re-registered version executes against the
            # previous version's globals dict still pinned in sys.modules.
            sys.modules.pop(module_name, None)

            logger.debug(f"Creating module: {module_name}")
            module_spec = importlib.util.spec_from_loader(module_name, loader=None)
            module = importlib.util.module_from_spec(module_spec)  # type: ignore
            sys.modules[module_name] = module

            logger.debug("Executing workflow source code")
            exec(source_code, module.__dict__)

            if self._module_cache_ttl > 0:
                self._module_cache[cache_key] = (module, time.monotonic())

        ctx = request.context
        self._setup_progress(ctx)

        for obj in module.__dict__.values():
            if (
                isinstance(obj, workflow)
                and obj.namespace == request.workflow.namespace
                and obj.name == request.workflow.name
            ):
                wfunc = obj
                break

        if wfunc:
            logger.debug(f"Found workflow: {request.workflow.name}")
            logger.debug(f"Executing workflow: {request.workflow.name}")
            task = asyncio.create_task(wfunc(request.context))
            logger.debug(f"Added async task for workflow execution: {request.workflow.name}")
            self._running_workflows[ctx.execution_id] = task
            start_time = asyncio.get_event_loop().time()
            logger.debug(f"Workflow execution started: {request.workflow.name}")
            try:
                ctx = await task
            finally:
                self._running_workflows.pop(request.context.execution_id, None)
                await self._teardown_progress(request.context.execution_id)
                logger.debug(f"Workflow execution async task removed: {request.workflow.name}")

            execution_time = asyncio.get_event_loop().time() - start_time
            logger.debug(f"Workflow execution completed in {execution_time:.4f}s")

            m = get_metrics()
            if m:
                status = "completed" if not ctx.has_failed else "failed"
                m.record_workflow_completed(
                    request.workflow.namespace,
                    request.workflow.name,
                    status,
                    execution_time,
                )
        else:
            logger.warning(f"Workflow {request.workflow.name} not found in module")
            raise WorkflowNotFoundError(f"Workflow {request.workflow.name} not found")

        return ctx

    async def _checkpoint(self, ctx: ExecutionContext):
        pending = self._pending_checkpoints.pop(ctx.execution_id, None)

        if pending and not pending.done():
            pending.cancel()
            try:
                await pending
            except (asyncio.CancelledError, Exception):
                pass

        if ctx.has_finished:
            await self._send_checkpoint(ctx)
        else:
            task = asyncio.create_task(self._send_checkpoint(ctx))
            task.add_done_callback(self._handle_checkpoint_error)
            self._pending_checkpoints[ctx.execution_id] = task

    def _handle_checkpoint_error(self, task: asyncio.Task):
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            logger.error(f"Checkpoint task failed: {exc}")

    async def _send_checkpoint(self, ctx: ExecutionContext):
        base_url = f"{self.base_url}/{self.name}"
        headers = {"Authorization": f"Bearer {self.session_token}"}
        try:
            logger.info(f"Checkpointing execution '{ctx.workflow_name}' ({ctx.execution_id})...")
            logger.debug(f"Checkpoint URL: {base_url}/checkpoint/{ctx.execution_id}")
            logger.debug(f"Checkpoint state: {ctx.state.value}")
            logger.debug(f"Number of events: {len(ctx.events)}")

            ctx_dict = ctx.to_dict()
            logger.debug(f"Sending checkpoint data ({len(str(ctx_dict))} bytes)")

            checkpoint_start = time.monotonic()
            response = await self.client.post(
                f"{base_url}/checkpoint/{ctx.execution_id}",
                json=ctx_dict,
                headers=headers,
            )
            response.raise_for_status()
            checkpoint_duration = time.monotonic() - checkpoint_start
            response_data = response.json()

            logger.debug(f"Checkpoint response: {response.status_code}")
            logger.debug(f"Response data: {response_data}")
            logger.info(
                f"Checkpoint for execution '{ctx.workflow_name}' ({ctx.execution_id}) completed successfully",
            )

            from flux.observability import get_metrics

            m = get_metrics()
            if m:
                m.record_checkpoint(ctx.workflow_namespace, ctx.workflow_name, checkpoint_duration)
        except Exception as e:
            logger.error(f"Error during checkpoint: {str(e)}")
            logger.debug(f"Checkpoint error details: {type(e).__name__}: {str(e)}")
            raise

    async def _flush_progress(self, queue: asyncio.Queue, execution_id: str):
        base_url = f"{self.base_url}/{self.name}"
        headers = {"Authorization": f"Bearer {self.session_token}"}
        try:
            while True:
                batch = []
                try:
                    item = await queue.get()
                    batch.append(item)
                    while not queue.empty() and len(batch) < 50:
                        batch.append(queue.get_nowait())
                except asyncio.CancelledError:
                    while not queue.empty():
                        batch.append(queue.get_nowait())
                    if batch:
                        try:
                            await self.client.post(
                                f"{base_url}/progress/{execution_id}",
                                json=batch,
                                headers=headers,
                            )
                        except Exception:
                            pass
                    return

                try:
                    await self.client.post(
                        f"{base_url}/progress/{execution_id}",
                        json=batch,
                        headers=headers,
                    )
                except Exception:
                    logger.debug(f"Failed to flush progress for {execution_id}")
        except Exception:
            pass

    def _setup_progress(self, ctx):
        queue = asyncio.Queue(maxsize=1000)
        self._progress_queues[ctx.execution_id] = queue

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None:
            self._progress_flushers[ctx.execution_id] = asyncio.create_task(
                self._flush_progress(queue, ctx.execution_id),
            )
        else:
            self._progress_flushers[ctx.execution_id] = None

        def on_progress(execution_id, task_id, task_name, value):
            q = self._progress_queues.get(execution_id)
            if q:
                try:
                    q.put_nowait(
                        {
                            "task_id": task_id,
                            "task_name": task_name,
                            "value": value,
                        },
                    )
                except asyncio.QueueFull:
                    pass

        ctx.set_progress_callback(on_progress)

    async def _teardown_progress(self, execution_id: str):
        flusher = self._progress_flushers.pop(execution_id, None)
        if flusher and not flusher.done():
            flusher.cancel()
            try:
                await flusher
            except asyncio.CancelledError:
                pass
        self._progress_queues.pop(execution_id, None)

    async def _get_runtime_info(self):
        logger.debug("Gathering runtime information")
        runtime_info = {
            "os_name": platform.system(),
            "os_version": platform.release(),
            "python_version": platform.python_version(),
        }
        logger.debug(f"Runtime info: {runtime_info}")
        return runtime_info

    async def _get_resources_info(self):
        logger.debug("Gathering system resource information")

        logger.debug("Getting CPU information")
        cpu_total = psutil.cpu_count(logical=True)
        cpu_percent = psutil.cpu_percent(interval=0.5)
        cpu_available = cpu_total * (100 - cpu_percent) / 100
        logger.debug(f"CPU: total={cpu_total}, usage={cpu_percent}%, available={cpu_available:.2f}")

        logger.debug("Getting memory information")
        memory = psutil.virtual_memory()
        memory_total = memory.total
        memory_available = memory.available
        logger.debug(
            f"Memory: total={memory_total}, available={memory_available}, percent={memory.percent}%",
        )

        logger.debug("Getting disk information")
        disk = psutil.disk_usage("/")
        disk_total = disk.total
        disk_free = disk.free
        logger.debug(f"Disk: total={disk_total}, free={disk_free}, percent={disk.percent}%")

        logger.debug("Getting GPU information")
        gpus = await self._get_gpu_info()

        resources = {
            "cpu_total": cpu_total,
            "cpu_available": cpu_available,
            "memory_total": memory_total,
            "memory_available": memory_available,
            "disk_total": disk_total,
            "disk_free": disk_free,
            "gpus": gpus,
        }

        logger.debug(f"Collected resource information: {len(gpus)} GPUs found")
        return resources

    async def _get_gpu_info(self):
        logger.debug("Collecting GPU information")
        try:
            import GPUtil
        except (ImportError, ModuleNotFoundError):
            logger.debug("GPUtil not available, skipping GPU info")
            return []

        gpus = []
        gpu_devices = GPUtil.getGPUs()
        logger.debug(f"Found {len(gpu_devices)} GPU devices")

        for i, gpu in enumerate(gpu_devices):
            logger.debug(
                f"GPU {i + 1}: {gpu.name}, Memory: {gpu.memoryTotal}MB, Free: {gpu.memoryFree}MB",
            )
            gpus.append(
                {
                    "name": gpu.name,
                    "memory_total": gpu.memoryTotal,
                    "memory_available": gpu.memoryFree,
                },
            )
        return gpus

    async def _get_installed_packages(self):
        logger.debug("Collecting installed packages information")
        import importlib.metadata

        # TODO: use poetry package groups to load a specific set of packages that are available in the worker environment for execution
        packages = []
        for dist in importlib.metadata.distributions():
            name = dist.metadata.get("Name")
            if name:  # Only include packages with a valid name
                packages.append({"name": name, "version": dist.version})

        logger.debug(f"Collected information for {len(packages)} installed packages")
        return packages


if __name__ == "__main__":  # pragma: no cover
    from uuid import uuid4
    from flux.utils import configure_logging

    configure_logging()
    settings = Configuration.get().settings
    worker_name = f"worker-{uuid4().hex[-6:]}"
    server_url = settings.workers.server_url

    logger.debug(f"Starting worker with name: {worker_name}")
    logger.debug(f"Server URL: {server_url}")
    logger.debug(
        f"Bootstrap token configured: {'Yes' if settings.workers.bootstrap_token else 'No'}",
    )

    Worker(name=worker_name, server_url=server_url).start()
