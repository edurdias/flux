"""Runner that executes the workflow as an asyncio task in the worker process."""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING

from flux.errors import WorkflowNotFoundError
from flux.runners.base import Runner, RunnerHooks
from flux.runners.loader import WorkflowModuleLoader, find_workflow
from flux.utils import get_logger

if TYPE_CHECKING:
    from flux.domain.execution_context import ExecutionContext
    from flux.worker import WorkflowExecutionRequest

logger = get_logger(__name__)


class InProcessRunner(Runner):
    name = "inprocess"

    def __init__(self, loader: WorkflowModuleLoader | None = None):
        self._loader = loader or WorkflowModuleLoader()

    async def execute(
        self,
        request: WorkflowExecutionRequest,
        hooks: RunnerHooks,
    ) -> ExecutionContext:
        module = self._loader.load(
            request.workflow.namespace,
            request.workflow.name,
            request.workflow.version,
            request.workflow.source,
        )
        wfunc = find_workflow(module, request.workflow.namespace, request.workflow.name)
        if wfunc is None:
            logger.warning(f"Workflow {request.workflow.name} not found in module")
            raise WorkflowNotFoundError(f"Workflow {request.workflow.name} not found")

        inner = asyncio.create_task(wfunc(request.context))
        try:
            return await inner
        except asyncio.CancelledError:
            # Cancellation lands on the runner task; forward it so the
            # workflow's own CancelledError handling (terminal CANCELLED
            # checkpoint) still runs.
            inner.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await inner
            raise
