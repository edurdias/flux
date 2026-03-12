from __future__ import annotations

import asyncio
import base64
import importlib
import platform
import random
import sys
from collections.abc import Awaitable
from typing import Callable

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


class WorkflowDefinition(BaseModel):
    id: str
    name: str
    version: int
    source: str


class WorkflowExecutionRequest(BaseModel):
    workflow: WorkflowDefinition
    context: ExecutionContext

    class Config:
        arbitrary_types_allowed = True

    @staticmethod
    def from_json(
        data: dict,
        checkpoint: Callable[[ExecutionContext], Awaitable],
    ) -> WorkflowExecutionRequest:
        return WorkflowExecutionRequest(
            workflow=WorkflowDefinition(**data["workflow"]),
            context=ExecutionContext(
                workflow_id=data["context"]["workflow_id"],
                workflow_name=data["context"]["workflow_name"],
                input=data["context"]["input"],
                execution_id=data["context"]["execution_id"],
                state=data["context"]["state"],
                events=[ExecutionEvent(**event) for event in data["context"]["events"]],
                checkpoint=checkpoint,
            ),
        )


class Worker:
    def __init__(self, name: str, server_url: str):
        self.name = name
        config = Configuration.get().settings.workers
        self.bootstrap_token = config.bootstrap_token
        self.base_url = f"{server_url or config.server_url}/workers"
        self.client = httpx.AsyncClient(timeout=config.default_timeout or None)
        self._running_workflows: dict[str, asyncio.Task] = {}
        self._pending_checkpoints: dict[str, asyncio.Task] = {}
        self._reconnect_max_delay = config.reconnect_max_delay

    def start(self):
        logger.info("Worker starting up...")
        logger.debug(f"Worker name: {self.name}")
        logger.debug(f"Server URL: {self.base_url}")
        try:
            asyncio.run(self._run())
        except KeyboardInterrupt:
            logger.info("Worker interrupted by user")
        finally:
            logger.info("Worker shutting down...")

    async def _run(self):
        """Main worker loop: register, connect, reconnect on failure."""
        backoff = 1
        while True:
            try:
                await self._register()
                await self._connect()
                break
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
                finally:
                    self._running_workflows.pop(context.execution_id, None)
        except Exception as ex:
            logger.error(f"Error handling execution_cancelled event: {str(ex)}")
            logger.debug(f"Exception details: {type(ex).__name__}: {str(ex)}", exc_info=True)

    async def _handle_execution_resumed(self, e):
        """Handle execution resumed event asynchronously.

        This method is called as a separate task and should handle its own exceptions.
        """
        try:
            logger.debug("Received execution_resumed event")
            request = WorkflowExecutionRequest.from_json(e.json(), self._checkpoint)
            logger.info(
                f"Resuming Execution - {request.workflow.name} v{request.workflow.version} - {request.context.execution_id}",
            )
            logger.debug(f"Workflow input: {request.context.input}")
            ctx = await self._execute_workflow(request)
            logger.debug(
                f"Workflow execution completed with state: {ctx.state.value}",
            )

            if ctx.has_failed:
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
            logger.error(f"Error handling execution_resumed event: {str(ex)}")
            logger.debug(f"Exception details: {type(ex).__name__}: {str(ex)}", exc_info=True)

    async def _handle_execution_scheduled(self, base_url, headers, e):
        """Handle execution scheduled event asynchronously.

        This method is called as a separate task and should handle its own exceptions.
        """
        try:
            logger.debug("Received execution_scheduled event")
            request = WorkflowExecutionRequest.from_json(e.json(), self._checkpoint)

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
            logger.debug(f"Claim response: {claim_data}")

            logger.info(
                f"Execution Claimed - {request.workflow.name} v{request.workflow.version} - {request.context.execution_id}",
            )

            logger.debug(f"Starting workflow execution: {request.workflow.name}")
            ctx = await self._execute_workflow(request)
            logger.debug(
                f"Workflow execution completed with state: {ctx.state.value}",
            )

            if ctx.has_failed:
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
        logger.debug(
            f"Preparing to execute workflow: {request.workflow.name} v{request.workflow.version}",
        )

        # Decode the source code
        source_code = base64.b64decode(request.workflow.source).decode("utf-8")
        logger.debug(f"Decoded workflow source code ({len(source_code)} bytes)")

        # Create a dynamic module for the workflow
        module_name = f"flux_workflow_{request.workflow.name}_{request.workflow.version}"
        logger.debug(f"Creating module: {module_name}")
        module_spec = importlib.util.spec_from_loader(module_name, loader=None)
        module = importlib.util.module_from_spec(module_spec)  # type: ignore
        sys.modules[module_name] = module

        # Execute the workflow code in the module context
        logger.debug("Executing workflow source code")
        exec(source_code, module.__dict__)

        ctx = request.context

        # Find the workflow by searching for a workflow object with matching name
        wfunc = None
        for obj in module.__dict__.values():
            if isinstance(obj, workflow) and obj.name == request.workflow.name:
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
                logger.debug(f"Workflow execution async task removed: {request.workflow.name}")

            execution_time = asyncio.get_event_loop().time() - start_time
            logger.debug(f"Workflow execution completed in {execution_time:.4f}s")
        else:
            logger.warning(f"Workflow {request.workflow.name} not found in module")
            raise WorkflowNotFoundError(f"Workflow {request.workflow.name} not found")

        return ctx

    async def _checkpoint(self, ctx: ExecutionContext):
        pending = self._pending_checkpoints.pop(ctx.execution_id, None)

        if ctx.has_finished:
            if pending and not pending.done():
                pending.cancel()
                try:
                    await pending
                except (asyncio.CancelledError, Exception):
                    pass
            await self._send_checkpoint(ctx)
        else:
            if pending and not pending.done():
                await pending
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

            response = await self.client.post(
                f"{base_url}/checkpoint/{ctx.execution_id}",
                json=ctx_dict,
                headers=headers,
            )
            response.raise_for_status()
            response_data = response.json()

            logger.debug(f"Checkpoint response: {response.status_code}")
            logger.debug(f"Response data: {response_data}")
            logger.info(
                f"Checkpoint for execution '{ctx.workflow_name}' ({ctx.execution_id}) completed successfully",
            )
        except Exception as e:
            logger.error(f"Error during checkpoint: {str(e)}")
            logger.debug(f"Checkpoint error details: {type(e).__name__}: {str(e)}")
            raise

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

        # Get CPU information
        logger.debug("Getting CPU information")
        cpu_total = psutil.cpu_count(logical=True)
        cpu_percent = psutil.cpu_percent(interval=0.5)
        cpu_available = cpu_total * (100 - cpu_percent) / 100
        logger.debug(f"CPU: total={cpu_total}, usage={cpu_percent}%, available={cpu_available:.2f}")

        # Get memory information
        logger.debug("Getting memory information")
        memory = psutil.virtual_memory()
        memory_total = memory.total
        memory_available = memory.available
        logger.debug(
            f"Memory: total={memory_total}, available={memory_available}, percent={memory.percent}%",
        )

        # Get disk information
        logger.debug("Getting disk information")
        disk = psutil.disk_usage("/")
        disk_total = disk.total
        disk_free = disk.free
        logger.debug(f"Disk: total={disk_total}, free={disk_free}, percent={disk.percent}%")

        # Get GPU information
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
        import GPUtil

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
