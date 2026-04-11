from __future__ import annotations

import re

from flux.domain.resource_request import ResourceRequest
from flux.domain.execution_context import ExecutionContext
from flux.context_managers import ContextManager
from flux.errors import PauseRequested
from flux.output_storage import OutputStorage
from flux.utils import maybe_awaitable
from flux.domain.schedule import Schedule

import asyncio
from functools import wraps
from typing import Any, Callable, TypeVar

F = TypeVar("F", bound=Callable[..., Any])

_NAMESPACE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_NAMESPACE_MAX_LEN = 64


def _validate_namespace(namespace: str | None) -> str:
    if namespace is None or namespace == "":
        return "default"
    if len(namespace) > _NAMESPACE_MAX_LEN:
        raise ValueError(
            f"Invalid namespace '{namespace}': max length {_NAMESPACE_MAX_LEN}",
        )
    if not _NAMESPACE_RE.match(namespace):
        raise ValueError(
            f"Invalid namespace '{namespace}': must match {_NAMESPACE_RE.pattern}",
        )
    return namespace


class workflow:
    @staticmethod
    def with_options(
        name: str | None = None,
        namespace: str | None = None,
        secret_requests: list[str] = [],
        output_storage: OutputStorage | None = None,
        requests: ResourceRequest | None = None,
        schedule: Schedule | None = None,
    ) -> Callable[[F], workflow]:
        """
        A decorator to configure options for a workflow function.

        Args:
            name (str | None, optional): The name of the workflow. Defaults to None.
            namespace (str | None, optional): The namespace for the workflow. Defaults to "default".
            secret_requests (list[str], optional): A list of secret keys required by the workflow. Defaults to an empty list.
            output_storage (OutputStorage | None, optional): The storage configuration for the workflow's output. Defaults to None.
            requests (ResourceRequest | None, optional): The minimum resources, runtime and packages for the workflow. Defaults to None.
            schedule (Schedule | None, optional): The schedule configuration for automatic workflow execution. Defaults to None.

        Returns:
            Callable[[F], workflow]: A decorator that wraps the given function into a workflow object with the specified options.
        """

        def wrapper(func: F) -> workflow:
            return workflow(
                func=func,
                name=name,
                namespace=namespace,
                secret_requests=secret_requests,
                output_storage=output_storage,
                requests=requests,
                schedule=schedule,
            )

        return wrapper

    def __init__(
        self,
        func: F,
        name: str | None = None,
        namespace: str | None = None,
        secret_requests: list[str] = [],
        output_storage: OutputStorage | None = None,
        requests: ResourceRequest | None = None,
        schedule: Schedule | None = None,
    ):
        self._func = func
        self._name = name if name else func.__name__
        self._namespace = _validate_namespace(namespace)
        self._secret_requests = secret_requests
        self._output_storage = output_storage
        self._requests = requests
        self._schedule = schedule
        wraps(func)(self)

    @property
    def name(self) -> str:
        return self._name

    @property
    def namespace(self) -> str:
        return self._namespace

    @property
    def qualified_name(self) -> str:
        return f"{self._namespace}/{self._name}"

    @property
    def secret_requests(self) -> list[str]:
        return self._secret_requests

    @property
    def output_storage(self) -> OutputStorage | None:
        return self._output_storage

    @property
    def requests(self) -> ResourceRequest | None:
        return self._requests

    @property
    def schedule(self) -> Schedule | None:
        return self._schedule

    async def __call__(self, ctx: ExecutionContext, *args) -> Any:
        if ctx.has_finished:
            return ctx

        self.id = f"{ctx.workflow_name}_{ctx.execution_id}"

        if ctx.is_paused and not ctx.is_resuming:
            ctx.start_resuming()
            await ctx.checkpoint()

        if not ctx.has_started:
            ctx.start(self.id)

        token = ExecutionContext.set(ctx)
        try:
            output = await maybe_awaitable(self._func(ctx))
            output_value = (
                self.output_storage.store(self.id, output) if self.output_storage else output
            )
            ctx.complete(self.id, output_value)
        except PauseRequested as ex:
            ctx.pause(self.id, ex.name, ex.output)
        except asyncio.CancelledError:
            ctx.cancel()
            raise
        except Exception as ex:
            ctx.fail(self.id, ex)
        finally:
            await ctx.checkpoint()
            ExecutionContext.reset(token)
        return ctx

    def run(self, *args, **kwargs) -> ExecutionContext:
        if "execution_id" in kwargs:
            return self.resume(kwargs["execution_id"])

        workflow_id = self._ensure_registered()

        ctx: ExecutionContext = ExecutionContext(
            workflow_id=workflow_id,
            workflow_name=self.name,
            input=args[0] if len(args) > 0 else None,
        )

        ctx.set_checkpoint(self._save)
        return asyncio.run(self(ctx))

    def _ensure_registered(self) -> str:
        """Ensure this workflow has a row in the ``workflows`` table.

        Inline ``workflow.run()`` calls (used in tests, scripts, and dev
        shells) historically left executions pointing at a ``workflow_id``
        that did not exist in the ``workflows`` table. SQLite let this
        slide because foreign-key enforcement is off by default; PostgreSQL
        rejects the insert. Register on first call and reuse the id on
        subsequent calls so ``executions.workflow_id`` always refers to a
        real row.
        """
        import inspect
        from pathlib import Path

        from sqlalchemy.exc import IntegrityError

        from flux.catalogs import WorkflowCatalog
        from flux.errors import WorkflowNotFoundError

        catalog = WorkflowCatalog.create()

        try:
            return catalog.get(self.name).id  # type: ignore[call-arg]  # TODO(Task 6): use catalog.get(self.namespace, self.name)
        except WorkflowNotFoundError:
            pass

        module = inspect.getmodule(self._func)
        source_file = getattr(module, "__file__", None) if module is not None else None
        if not source_file:
            raise RuntimeError(
                f"Cannot register workflow '{self.name}': the defining module "
                "has no readable source file. Workflows must be importable "
                "from a .py file to be run inline.",
            )

        source = Path(source_file).read_bytes()
        infos = catalog.parse(source)
        matching = next((w for w in infos if w.name == self.name), None)
        if matching is None:
            raise RuntimeError(
                f"Cannot register workflow '{self.name}': not found in "
                f"source file {source_file}.",
            )

        try:
            catalog.save([matching])
        except IntegrityError:
            # Lost a race with another registrant — fall through to re-read.
            pass
        return catalog.get(self.name).id  # type: ignore[call-arg]  # TODO(Task 6): use catalog.get(self.namespace, self.name)

    def resume(self, execution_id: str, input: Any = None) -> ExecutionContext:
        """
        Resume a paused workflow with the given execution ID and optional input.

        Args:
            execution_id (str): The ID of the workflow execution to resume.
            input (Any, optional): Input to provide when resuming the workflow. Defaults to None.

        Returns:
            ExecutionContext: The updated execution context after resuming the workflow.
        """
        ctx = ContextManager.create().get(execution_id)
        if input is not None:
            ctx.start_resuming(input)
            asyncio.run(ctx.checkpoint())
        ctx.set_checkpoint(self._save)
        return asyncio.run(self(ctx))

    def _save(self, ctx: ExecutionContext):
        ContextManager.create().save(ctx)
