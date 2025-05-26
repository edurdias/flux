from __future__ import annotations

from flux import ExecutionContext
from flux.cache import CacheManager
from flux.domain.events import ExecutionEvent, ExecutionEventType
from flux.errors import ExecutionError, ExecutionTimeoutError, PauseRequested, RetryError
from flux.output_storage import OutputStorage
from flux.secret_managers import SecretManager
from flux.utils import get_func_args, make_hashable, maybe_awaitable


import asyncio
from functools import wraps
from typing import Any, Callable, TypeVar

F = TypeVar("F", bound=Callable[..., Any])


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

        task_id = f"{full_name}_{abs(hash((full_name, make_hashable(task_args), make_hashable(args), make_hashable(kwargs))))}"

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
