"""Runner interface and the hooks runners use to reach the worker."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from flux.domain.execution_context import ExecutionContext
    from flux.worker import WorkflowExecutionRequest


@dataclass
class RunnerHooks:
    """Parent-side callables a runner routes execution I/O through.

    ``checkpoint`` is the worker's outbox entry point — delta encoding,
    claim-generation fencing, retry, terminal blocking, and transient
    suppression all live behind it, so the server cannot tell runners apart.
    ``get_secrets``/``get_configs`` resolve against the server with the
    worker's credentials; child processes never hold a network credential.
    """

    checkpoint: Callable[[ExecutionContext], Awaitable[Any]]
    get_secrets: Callable[[list[str]], Awaitable[dict[str, Any]]]
    get_configs: Callable[[list[str]], Awaitable[dict[str, Any]]]
    progress: Callable[[str, str, str, Any], None] | None = None


class Runner(ABC):
    """Executes one claimed workflow and returns its final context.

    Contract:
    - ``execute`` is awaited inside the task the worker registers in
      ``_running_workflows``; cancellation of that task must propagate into
      the workflow (in-process: cancel the inner task; subprocess: SIGTERM,
      grace, SIGKILL) and still deliver the terminal checkpoint.
    - A child runtime dying without a result raises ``WorkerProcessCrashed``;
      the worker maps it to the execution's durability semantics.
    """

    name: ClassVar[str]

    @abstractmethod
    async def execute(
        self,
        request: WorkflowExecutionRequest,
        hooks: RunnerHooks,
    ) -> ExecutionContext:  # pragma: no cover
        raise NotImplementedError()
