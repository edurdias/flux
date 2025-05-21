from __future__ import annotations

import asyncio
import base64
import importlib
import platform
import sys
from collections.abc import Awaitable
from typing import Callable

import click
import httpx
import psutil
from httpx_sse import aconnect_sse
from pydantic import BaseModel

from flux import decorators
from flux.config import Configuration
from flux.domain.events import ExecutionEvent
from flux.domain.execution_context import ExecutionContext


class WorkflowDefinition(BaseModel):
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
                name=data["context"]["name"],
                input=data["context"]["input"],
                execution_id=data["context"]["execution_id"],
                state=data["context"]["state"],
                events=[ExecutionEvent(**event) for event in data["context"]["events"]],
                checkpoint=checkpoint,
            ),
        )


class WorkerServer:
    def __init__(self, name: str, control_plane_url: str, echo: Callable):
        self.name = name
        self.echo = echo
        config = Configuration.get().settings.workers
        self.bootstrap_token = config.bootstrap_token
        self.base_url = f"{control_plane_url or config.control_plane_url}/workers"
        self.client = httpx.AsyncClient(timeout=30.0)

    def start(self):
        try:
            self.echo("Worker starting up...")
            asyncio.run(self._start())
            self.echo("Worker shutting down...")
        except Exception:
            self.echo("Worker shutting down...")

    async def _start(self):
        try:
            await self._register_with_control_plane()
            await self._start_sse_connection()
        except KeyboardInterrupt:
            raise
        except Exception:
            raise

    async def _register_with_control_plane(self):
        try:
            self.echo(f"Registering worker '{self.name}' with control plane...   ", nl=False)

            runtime = await self._get_runtime_info()
            resources = await self._get_resources_info()
            packages = await self._get_installed_packages()

            registration = {
                "name": self.name,
                "runtime": runtime,
                "resources": resources,
                "packages": packages,
            }

            response = await self.client.post(
                f"{self.base_url}/register",
                json=registration,
                headers={"Authorization": f"Bearer {self.bootstrap_token}"},
            )
            response.raise_for_status()
            data = response.json()
            self.session_token = data["session_token"]
            self.echo("OK")
        except Exception:
            self.echo("ERROR", err=True)
            raise

    async def _start_sse_connection(self):
        """Connect to SSE endpoint and handle events asynchronously"""
        self.echo("Establishing connection with control plane...   ", nl=False)

        base_url = f"{self.base_url}/{self.name}"
        headers = {"Authorization": f"Bearer {self.session_token}"}

        try:
            async with httpx.AsyncClient(timeout=None) as client:
                async with aconnect_sse(
                    client,
                    "GET",
                    f"{base_url}/connect",
                    headers=headers,
                ) as es:
                    self.echo("OK")
                    async for e in es.aiter_sse():
                        if e.event == "execution_scheduled":
                            request = WorkflowExecutionRequest.from_json(e.json(), self._checkpoint)

                            self.echo(
                                f"Execution Scheduled - {request.workflow.name} v{request.workflow.version} - {request.context.execution_id}",
                            )

                            response = await self.client.post(
                                f"{base_url}/claim/{request.context.execution_id}",
                                headers=headers,
                            )
                            response.raise_for_status()

                            self.echo(
                                f"Execution Claimed - {request.workflow.name} v{request.workflow.version} - {request.context.execution_id}",
                            )

                            source_code = base64.b64decode(request.workflow.source).decode("utf-8")
                            module_name = (
                                f"flux_workflow_{request.workflow.name}_{request.workflow.version}"
                            )
                            module_spec = importlib.util.spec_from_loader(module_name, loader=None)
                            module = importlib.util.module_from_spec(module_spec)
                            sys.modules[module_name] = module
                            exec(source_code, module.__dict__)

                            if request.workflow.name in module.__dict__:
                                workflow = module.__dict__[request.workflow.name]
                                if isinstance(workflow, decorators.workflow):
                                    ctx: ExecutionContext = await workflow(request.context)
                                    self.echo(
                                        f"Execution {ctx.state.value} - {request.workflow.name} v{request.workflow.version} - {request.context.execution_id}",
                                        err=ctx.has_failed,
                                    )

                        if e.event == "keep-alive":
                            self.echo("Event received: Keep-alive")

                        if e.event == "error":
                            self.echo("Event received: Error", err=True)
                            self.echo(e.data, err=True)

        except Exception as e:
            self.echo(e, err=True)
            raise

    async def _checkpoint(self, ctx: ExecutionContext):
        base_url = f"{self.base_url}/{self.name}"
        headers = {"Authorization": f"Bearer {self.session_token}"}
        try:
            self.echo(f"Checkpointing workflow '{ctx.name}'...   ", nl=False)
            response = await self.client.post(
                f"{base_url}/checkpoint/{ctx.execution_id}",
                json=ctx.to_dict(),
                headers=headers,
            )
            response.raise_for_status()
            self.echo("OK")
        except Exception as e:
            self.echo(e, err=True)
            raise

    async def _get_runtime_info(self):
        return {
            "os_name": platform.system(),
            "os_version": platform.release(),
            "python_version": platform.python_version(),
        }

    async def _get_resources_info(self):
        # Get CPU information
        cpu_total = psutil.cpu_count(logical=True)
        cpu_percent = psutil.cpu_percent(interval=0.5)
        cpu_available = cpu_total * (100 - cpu_percent) / 100

        # Get memory information
        memory = psutil.virtual_memory()
        memory_total = memory.total
        memory_available = memory.available

        # Get disk information
        disk = psutil.disk_usage("/")
        disk_total = disk.total
        disk_free = disk.free

        return {
            "cpu_total": cpu_total,
            "cpu_available": cpu_available,
            "memory_total": memory_total,
            "memory_available": memory_available,
            "disk_total": disk_total,
            "disk_free": disk_free,
            "gpus": await self._get_gpu_info(),
        }

    async def _get_gpu_info(self):
        import GPUtil

        gpus = []
        for gpu in GPUtil.getGPUs():
            gpus.append(
                {
                    "name": gpu.name,
                    "memory_total": gpu.memoryTotal,
                    "memory_available": gpu.memoryFree,
                },
            )
        return gpus

    async def _get_installed_packages(self):
        import pkg_resources  # type: ignore[import]

        # TODO: use poetry package groups to load a specific set of packages that are available in the worker environment for execution
        packages = []
        for dist in pkg_resources.working_set:
            packages.append({"name": dist.project_name, "version": dist.version})
        return packages


if __name__ == "__main__":  # pragma: no cover
    from uuid import uuid4

    settings = Configuration.get().settings
    WorkerServer(
        name=f"worker-{uuid4().hex[-6:]}",
        control_plane_url=settings.workers.control_plane_url,
        echo=click.echo,
    ).start()
