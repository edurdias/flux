from __future__ import annotations

# Import ExecutionContext directly to avoid circular imports
from flux.domain.resource_request import ResourceRequest
from flux.domain.execution_context import ExecutionContext
from flux.context_managers import ContextManager
from flux.errors import PauseRequested
from flux.output_storage import OutputStorage
from flux.utils import maybe_awaitable

import asyncio
from functools import wraps
from typing import Any, Callable, TypeVar

F = TypeVar("F", bound=Callable[..., Any])


class workflow:
    @staticmethod
    def with_options(
        name: str | None = None,
        secret_requests: list[str] = [],
        output_storage: OutputStorage | None = None,
        requests: ResourceRequest | None = None,
    ) -> Callable[[F], workflow]:
        """
        A decorator to configure options for a workflow function.

        Args:
            name (str | None, optional): The name of the workflow. Defaults to None.
            secret_requests (list[str], optional): A list of secret keys required by the workflow. Defaults to an empty list.
            output_storage (OutputStorage | None, optional): The storage configuration for the workflow's output. Defaults to None.
            requests (WorkflowRequests | None, optional): The requests minimum resources, runtime and packages for the workflow. Defaults to None.

        Returns:
            Callable[[F], workflow]: A decorator that wraps the given function into a workflow object with the specified options.
        """

        def wrapper(func: F) -> workflow:
            return workflow(
                func=func,
                name=name,
                secret_requests=secret_requests,
                output_storage=output_storage,
                requests=requests,
            )

        return wrapper

    def __init__(
        self,
        func: F,
        name: str | None = None,
        secret_requests: list[str] = [],
        output_storage: OutputStorage | None = None,
        requests: ResourceRequest | None = None,
    ):
        self._func = func
        self._name = name if name else func.__name__
        self._secret_requests = secret_requests
        self._output_storage = output_storage
        self._requests = requests
        wraps(func)(self)

    @property
    def name(self) -> str:
        return self._name

    @property
    def secret_requests(self) -> list[str]:
        return self._secret_requests

    @property
    def output_storage(self) -> OutputStorage | None:
        return self._output_storage

    @property
    def requests(self) -> ResourceRequest | None:
        return self._requests

    async def __call__(self, ctx: ExecutionContext, *args) -> Any:
        if ctx.has_finished:
            return ctx

        self.id = f"{ctx.workflow_name}_{ctx.execution_id}"

        if ctx.is_paused:
            ctx.resume(self.id)
        elif not ctx.has_started:
            ctx.start(self.id)

        token = ExecutionContext.set(ctx)
        try:
            output = await maybe_awaitable(self._func(ctx))
            output_value = (
                self.output_storage.store(self.id, output) if self.output_storage else output
            )
            ctx.complete(self.id, output_value)
        except PauseRequested as ex:
            ctx.pause(self.id, ex.name)
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
        async def save(ctx: ExecutionContext):
            return ContextManager.create().save(ctx)

        if "execution_id" in kwargs:
            ctx = ContextManager.create().get(kwargs["execution_id"])
        else:
            ctx = ExecutionContext(
                workflow_id=self.name,
                workflow_name=self.name,
                input=args[0] if len(args) > 0 else None,
            )
        ctx.set_checkpoint(save)
        return asyncio.run(self(ctx))
