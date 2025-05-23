from __future__ import annotations

import asyncio
import inspect
from functools import wraps
from typing import Any
from typing import Callable
from typing import TypeVar

from flux import ExecutionContext
from flux.cache import CacheManager
from flux.context_managers import ContextManager
from flux.domain.events import ExecutionEvent
from flux.domain.events import ExecutionEventType
from flux.errors import ExecutionError
from flux.errors import ExecutionTimeoutError
from flux.errors import PauseRequested
from flux.errors import RetryError
from flux.output_storage import OutputStorage
from flux.secret_managers import SecretManager
from flux.utils import make_hashable
from flux.utils import maybe_awaitable

F = TypeVar("F", bound=Callable[..., Any])


def get_func_args(func: Callable, args: tuple) -> dict:
    arg_names = inspect.getfullargspec(func).args
    arg_values: list[Any] = []

    for arg in args:
        if isinstance(arg, workflow):
            arg_values.append(arg.name)
        elif inspect.isclass(type(arg)) and isinstance(arg, Callable):  # type: ignore[arg-type]
            arg_values.append(arg)
        elif isinstance(arg, Callable):  # type: ignore[arg-type]
            arg_values.append(arg.__name__)
        elif isinstance(arg, list):
            arg_values.append(tuple(arg))
        else:
            arg_values.append(arg)

    return dict(zip(arg_names, arg_values))


class workflow:
    @staticmethod
    def with_options(
        name: str | None = None,
        secret_requests: list[str] = [],
        output_storage: OutputStorage | None = None,
    ) -> Callable[[F], workflow]:
        """
        A decorator to configure options for a workflow function.

        Args:
            name (str | None, optional): The name of the workflow. Defaults to None.
            secret_requests (list[str], optional): A list of secret keys required by the workflow. Defaults to an empty list.
            output_storage (OutputStorage | None, optional): The storage configuration for the workflow's output. Defaults to None.

        Returns:
            Callable[[F], workflow]: A decorator that wraps the given function into a workflow object with the specified options.
        """

        def wrapper(func: F) -> workflow:
            return workflow(
                func=func,
                name=name,
                secret_requests=secret_requests,
                output_storage=output_storage,
            )

        return wrapper

    def __init__(
        self,
        func: F,
        name: str | None = None,
        secret_requests: list[str] = [],
        output_storage: OutputStorage | None = None,
    ):
        self._func = func
        self.name = name if name else func.__name__
        self.secret_requests = secret_requests
        self.output_storage = output_storage
        wraps(func)(self)

    async def __call__(self, ctx: ExecutionContext, *args) -> Any:
        if ctx.has_finished:
            return ctx

        self.id = f"{ctx.name}_{ctx.execution_id}"

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
        except Exception as ex:
            ctx.fail(self.id, ex)
        finally:
            ExecutionContext.reset(token)

        await ctx.checkpoint()
        return ctx

    def run(self, *args, **kwargs) -> ExecutionContext:
        async def save(ctx: ExecutionContext):
            return ContextManager.create().save(ctx)

        if "execution_id" in kwargs:
            ctx = ContextManager.create().get(kwargs["execution_id"])
        else:
            ctx = ExecutionContext(
                self.name,
                input=args[0] if len(args) > 0 else None,
            )
        ctx.set_checkpoint(save)
        return asyncio.run(self(ctx))


class TaskMetadata:
    def __init__(self, task_id: str, task_name: str):
        self.task_id = task_id
        self.task_name = task_name

    def __repr__(self):
        return f"TaskMetadata(task_id={self.task_id}, task_name={self.task_name})"


class task:
    @staticmethod
    def with_options(
        name: str | None = None,
        fallback: Callable | None = None,
        rollback: Callable | None = None,
        retry_max_attempts: int = 0,
        retry_delay: int = 1,
        retry_backoff: int = 2,
        timeout: int = 0,
        secret_requests: list[str] = [],
        output_storage: OutputStorage | None = None,
        cache: bool = False,
        metadata: bool = False,
    ) -> Callable[[F], task]:
        def wrapper(func: F) -> task:
            return task(
                func=func,
                name=name,
                fallback=fallback,
                rollback=rollback,
                retry_max_attempts=retry_max_attempts,
                retry_delay=retry_delay,
                retry_backoff=retry_backoff,
                timeout=timeout,
                secret_requests=secret_requests,
                output_storage=output_storage,
                cache=cache,
                metadata=metadata,
            )

        return wrapper

    def __init__(
        self,
        func: F,
        name: str | None = None,
        fallback: Callable | None = None,
        rollback: Callable | None = None,
        retry_max_attempts: int = 0,
        retry_delay: int = 1,
        retry_backoff: int = 2,
        timeout: int = 0,
        secret_requests: list[str] = [],
        output_storage: OutputStorage | None = None,
        cache: bool = False,
        metadata: bool = False,
    ):
        self._func = func
        self.name = name if name else func.__name__
        self.fallback = fallback
        self.rollback = rollback
        self.retry_max_attempts = retry_max_attempts
        self.retry_delay = retry_delay
        self.retry_backoff = retry_backoff
        self.timeout = timeout
        self.secret_requests = secret_requests
        self.output_storage = output_storage
        self.cache = cache
        self.metadata = metadata
        wraps(func)(self)

    def __get__(self, instance, owner):
        return lambda *args, **kwargs: self(
            *(args if instance is None else (instance,) + args),
            **kwargs,
        )

    async def __call__(self, *args, **kwargs) -> Any:
        task_args = get_func_args(self._func, args)
        full_name = self.name.format(**task_args)

        task_id = (
            f"{full_name}_{abs(hash((full_name, make_hashable(task_args), make_hashable(kwargs))))}"
        )

        ctx = await ExecutionContext.get()

        finished = [
            e
            for e in ctx.events
            if e.source_id == task_id
            and e.type
            in (
                ExecutionEventType.TASK_COMPLETED,
                ExecutionEventType.TASK_FAILED,
            )
        ]

        if len(finished) > 0:
            return finished[0].value

        if not ctx.has_resumed:
            ctx.events.append(
                ExecutionEvent(
                    type=ExecutionEventType.TASK_STARTED,
                    source_id=task_id,
                    name=full_name,
                    value=task_args,
                ),
            )

        try:
            output = None
            if self.cache:
                output = CacheManager.get(task_id)

            if not output:
                if self.secret_requests:
                    secrets = SecretManager.current().get(self.secret_requests)
                    kwargs = {**kwargs, "secrets": secrets}

                if self.metadata:
                    kwargs = {**kwargs, "metadata": TaskMetadata(task_id, full_name)}

                if self.timeout > 0:
                    try:
                        output = await asyncio.wait_for(
                            maybe_awaitable(self._func(*args, **kwargs)),
                            timeout=self.timeout,
                        )
                    except asyncio.TimeoutError as ex:
                        raise ExecutionTimeoutError(
                            "Task",
                            self.name,
                            task_id,
                            self.timeout,
                        ) from ex
                else:
                    output = await maybe_awaitable(self._func(*args, **kwargs))

                if self.cache:
                    CacheManager.set(task_id, output)

        except Exception as ex:
            output = await self.__handle_exception(
                ctx,
                ex,
                task_id,
                full_name,
                task_args,
                args,
                kwargs,
            )

        ctx.events.append(
            ExecutionEvent(
                type=ExecutionEventType.TASK_COMPLETED,
                source_id=task_id,
                name=full_name,
                value=self.output_storage.store(task_id, output) if self.output_storage else output,
            ),
        )

        await ctx.checkpoint()
        return output

    async def map(self, args):
        return await asyncio.gather(*(self(arg) for arg in args))

    async def __handle_exception(
        self,
        ctx: ExecutionContext,
        ex: Exception,
        task_id: str,
        task_full_name: str,
        task_args: dict,
        args: tuple,
        kwargs: dict,
        retry_attempts: int = 0,
    ):
        if isinstance(ex, PauseRequested):
            ctx.events.append(
                ExecutionEvent(
                    type=ExecutionEventType.TASK_PAUSED,
                    source_id=task_id,
                    name=task_full_name,
                    value=ex.name,
                ),
            )
            await ctx.checkpoint()
            raise ex

        try:
            if self.retry_max_attempts > 0 and retry_attempts < self.retry_max_attempts:
                return await self.__handle_retry(
                    ctx,
                    task_id,
                    task_full_name,
                    args,
                    kwargs,
                )
            elif self.fallback:
                return await self.__handle_fallback(
                    ctx,
                    task_id,
                    task_full_name,
                    task_args,
                    args,
                    kwargs,
                )
            else:
                await self.__handle_rollback(
                    ctx,
                    task_id,
                    task_full_name,
                    task_args,
                    args,
                    kwargs,
                )

                ctx.events.append(
                    ExecutionEvent(
                        type=ExecutionEventType.TASK_FAILED,
                        source_id=task_id,
                        name=task_full_name,
                        value=ex,
                    ),
                )
                if isinstance(ex, ExecutionError):
                    raise ex
                raise ExecutionError(ex)

        except RetryError as ex:
            output = await self.__handle_exception(
                ctx,
                ex,
                task_id,
                task_full_name,
                task_args,
                args,
                kwargs,
                retry_attempts=ex.retry_attempts,
            )
            return output

    async def __handle_fallback(
        self,
        ctx: ExecutionContext,
        task_id: str,
        task_full_name: str,
        task_args: dict,
        args: tuple,
        kwargs: dict,
    ):
        if self.fallback:
            ctx.events.append(
                ExecutionEvent(
                    type=ExecutionEventType.TASK_FALLBACK_STARTED,
                    source_id=task_id,
                    name=task_full_name,
                    value=task_args,
                ),
            )
            try:
                output = await maybe_awaitable(self.fallback(*args, **kwargs))
                ctx.events.append(
                    ExecutionEvent(
                        type=ExecutionEventType.TASK_FALLBACK_COMPLETED,
                        source_id=task_id,
                        name=task_full_name,
                        value=self.output_storage.store(task_id, output)
                        if self.output_storage
                        else output,
                    ),
                )
            except Exception as ex:
                ctx.events.append(
                    ExecutionEvent(
                        type=ExecutionEventType.TASK_FALLBACK_FAILED,
                        source_id=task_id,
                        name=task_full_name,
                        value=ex,
                    ),
                )
                if isinstance(ex, ExecutionError):
                    raise ex
                raise ExecutionError(ex)

            return output

    async def __handle_rollback(
        self,
        ctx: ExecutionContext,
        task_id: str,
        task_full_name: str,
        task_args: dict,
        args: tuple,
        kwargs: dict,
    ):
        if self.rollback:
            ctx.events.append(
                ExecutionEvent(
                    type=ExecutionEventType.TASK_ROLLBACK_STARTED,
                    source_id=task_id,
                    name=task_full_name,
                    value=task_args,
                ),
            )
            try:
                output = await maybe_awaitable(self.rollback(*args, **kwargs))
                ctx.events.append(
                    ExecutionEvent(
                        type=ExecutionEventType.TASK_ROLLBACK_COMPLETED,
                        source_id=task_id,
                        name=task_full_name,
                        value=self.output_storage.store(task_id, output)
                        if self.output_storage
                        else output,
                    ),
                )
                return output
            except Exception as ex:
                ctx.events.append(
                    ExecutionEvent(
                        type=ExecutionEventType.TASK_ROLLBACK_FAILED,
                        source_id=task_id,
                        name=task_full_name,
                        value=ex,
                    ),
                )
                raise ex

    async def __handle_retry(
        self,
        ctx: ExecutionContext,
        task_id: str,
        task_full_name: str,
        args: tuple,
        kwargs: dict,
    ):
        attempt = 0
        while attempt < self.retry_max_attempts:
            attempt += 1
            current_delay = self.retry_delay
            retry_args = {
                "current_attempt": attempt,
                "max_attempts": self.retry_max_attempts,
                "current_delay": current_delay,
                "backoff": self.retry_backoff,
            }

            try:
                await asyncio.sleep(current_delay)
                current_delay = min(
                    current_delay * self.retry_backoff,
                    600,
                )

                ctx.events.append(
                    ExecutionEvent(
                        type=ExecutionEventType.TASK_RETRY_STARTED,
                        source_id=task_id,
                        name=task_full_name,
                        value=retry_args,
                    ),
                )
                output = await maybe_awaitable(self._func(*args, **kwargs))
                ctx.events.append(
                    ExecutionEvent(
                        type=ExecutionEventType.TASK_RETRY_COMPLETED,
                        source_id=task_id,
                        name=task_full_name,
                        value={
                            "current_attempt": attempt,
                            "max_attempts": self.retry_max_attempts,
                            "current_delay": current_delay,
                            "backoff": self.retry_backoff,
                            "output": self.output_storage.store(task_id, output)
                            if self.output_storage
                            else output,
                        },
                    ),
                )
                return output
            except Exception as ex:
                ctx.events.append(
                    ExecutionEvent(
                        type=ExecutionEventType.TASK_RETRY_FAILED,
                        source_id=task_id,
                        name=task_full_name,
                        value={
                            "current_attempt": attempt,
                            "max_attempts": self.retry_max_attempts,
                            "current_delay": current_delay,
                            "backoff": self.retry_backoff,
                        },
                    ),
                )
                if attempt == self.retry_max_attempts:
                    raise RetryError(
                        ex,
                        self.retry_max_attempts,
                        self.retry_delay,
                        self.retry_backoff,
                    )
