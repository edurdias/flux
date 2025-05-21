from __future__ import annotations

import asyncio
import base64
from typing import Any
from typing import Callable

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

from flux.catalogs import WorkflowCatalog
from flux.config import Configuration
from flux.context_managers import ContextManager
from flux.domain.execution_context import ExecutionContext
from flux.servers.models import ExecutionContext as ExecutionContextDTO
from flux.utils import to_json
from flux.worker_registry import WorkerInfo
from flux.worker_registry import WorkerRegistry


class ControlPlaneServer:
    """
    Control Plane for managing workflows and tasks.
    """

    def __init__(self, host: str, port: int, echo: Callable):
        self.host = host
        self.port = port
        self.echo = echo

    def start(self):
        """
        Start the control plane server.
        """
        self.echo(f"Starting control-plane server at {self.host}:{self.port}")
        uvicorn.run(self._create_api(), host=self.host, port=self.port)

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

    def _create_api(self) -> FastAPI:
        api = FastAPI(
            title="Flux API",
            version="1.0.0",
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

            class Config:
                arbitrary_types_allowed = True

        @api.post("/workflows")
        async def workflows_save(file: UploadFile = File(...)):
            source = await file.read()
            self.echo(f"Received file: {file.filename} with size: {len(source)} bytes:")
            try:
                catalog = WorkflowCatalog.create()
                workflows = catalog.parse(source)
                return catalog.save(workflows)
            except SyntaxError as e:
                raise HTTPException(status_code=400, detail=str(e))
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Error saving workflow: {str(e)}")

        @api.post("/workflows/{workflow_name}/start")
        async def workflows_start(
            workflow_name: str,
            input: Any = Body(...),
            wait: bool = False,
            detail: bool = False,
        ):
            """
            Start a workflow execution.
            This endpoint initiates a new workflow execution with the specified name and input data.
            It creates a workflow execution context and returns a summary of the created execution.
            Args:
                workflow_name (str): The name of the workflow to execute.
                input (Any): The input data for the workflow execution.
                wait (bool): Whether to wait for the workflow execution to complete before returning.
                detail (bool): Whether to return detailed information about the workflow execution context.
            Returns:
                dict: A summary of the workflow execution context.
            Raises:
                HTTPException: If there's an error scheduling the workflow.
            """
            try:
                manager = ContextManager.create()
                ctx = manager.save(ExecutionContext(workflow_name, input))

                if wait:
                    # Poll for workflow completion
                    backoff_factor = 1.5
                    current_delay = 0.1
                    max_delay = 2.0

                    while not ctx.has_finished:
                        await asyncio.sleep(current_delay)
                        ctx = manager.get(ctx.execution_id)
                        # Apply exponential backoff with a maximum delay
                        current_delay = min(current_delay * backoff_factor, max_delay)

                dto = ExecutionContextDTO.from_domain(ctx)
                return dto.summary() if not detail else dto

            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Error scheduling workflow: {str(e)}")

        @api.get("/workflows/{workflow_name}/inspect/{execution_id}")
        async def workflows_inspect(workflow_name: str, execution_id: str, detail: bool = False):
            try:
                manager = ContextManager.create()
                context = manager.get(execution_id)
                dto = ExecutionContextDTO.from_domain(context)
                return dto.summary() if not detail else dto
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Error inspecting workflow: {str(e)}")

        @api.post("/workers/register")
        async def workers_register(
            registration: WorkerRegistration,
            authorization: str = Header(None),
        ):
            try:
                token = self._extract_token(authorization)
                settings = Configuration.get().settings
                if settings.workers.bootstrap_token != token:
                    raise HTTPException(
                        status_code=403,
                        detail="Invalid bootstrap token.",
                    )

                registry = WorkerRegistry.create()
                return registry.register(
                    registration.name,
                    registration.runtime,
                    registration.packages,
                    registration.resources,
                )
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
                worker = self._get_worker(name, authorization)

                async def check_for_new_executions():
                    current_delay = 0.1
                    backoff_factor = 1
                    max_delay = 5

                    context_manager = ContextManager.create()
                    while True:
                        try:
                            ctx = context_manager.next_pending_execution(worker)
                            if ctx:
                                workflow = WorkflowCatalog.create().get(ctx.name)
                                workflow.source = base64.b64encode(workflow.source).decode("utf-8")

                                yield {
                                    "event": "execution_scheduled",
                                    "data": to_json({"workflow": workflow, "context": ctx}),
                                }

                                current_delay = 1
                                continue
                            # else:
                            #     yield {
                            #         "event": "keep-alive",
                            #         "data": "",
                            #     }
                            await asyncio.sleep(current_delay)
                            current_delay = min(current_delay * backoff_factor, max_delay)
                        except Exception as e:
                            yield {
                                "event": "error",
                                "data": str(e),
                            }
                            await asyncio.sleep(5)

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
                worker = self._get_worker(name, authorization)
                context_manager = ContextManager.create()
                ctx = context_manager.claim(execution_id, worker)
                return ctx.summary()
            except Exception as e:
                raise HTTPException(status_code=404, detail=str(e))

        @api.post("/workers/{name}/checkpoint/{execution_id}")
        async def workers_checkpoint(
            name: str,
            execution_id: str,
            context: ExecutionContextDTO = Body(...),
            authorization: str = Header(None),
        ):
            try:
                self._get_worker(name, authorization)
                context_manager = ContextManager.create()
                ctx = context_manager.get(execution_id)
                if not ctx:
                    raise HTTPException(status_code=404, detail="Execution context not found.")

                # Use Pydantic model for automatic datetime conversion
                domain_ctx = context.to_domain()

                ctx = context_manager.save(domain_ctx)
                return ctx.summary()
            except Exception as e:
                self.echo(e, err=True)
                raise HTTPException(status_code=400, detail=str(e))

        return api


if __name__ == "__main__":  # pragma: no cover
    settings = Configuration.get().settings
    ControlPlaneServer(
        settings.server_host,
        settings.server_port,
        print,
    ).start()
