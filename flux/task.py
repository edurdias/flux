from __future__ import annotations

from flux import ExecutionContext
from flux.cache import CacheManager
from flux.domain.events import ExecutionEvent, ExecutionEventType
from flux.errors import ExecutionError, ExecutionTimeoutError, PauseRequested, RetryError
from flux.output_storage import InlineOutputStorage, OutputStorage, OutputStorageReference
from flux.secret_managers import SecretManager
from flux.utils import get_func_args, make_hashable, maybe_awaitable


import asyncio
import time
from functools import wraps
from typing import Any, Callable, TypeVar
from urllib.parse import quote

F = TypeVar("F", bound=Callable[..., Any])

_auth_http_client = None


def _get_auth_http_client():
    """Return a process-wide httpx.AsyncClient for runtime authorization callbacks.

    Reused across task invocations so repeated task calls within a workflow share
    TCP/TLS connections instead of reconnecting for every authorize check.
    """
    global _auth_http_client
    if _auth_http_client is None:
        import httpx

        _auth_http_client = httpx.AsyncClient(timeout=10.0)
    return _auth_http_client


class TaskMetadata:
    def __init__(self, task_id: str, task_name: str):
        self.task_id = task_id
        self.task_name = task_name

    def __repr__(self):
        return f"TaskMetadata(task_id={self.task_id}, task_name={self.task_name})"


class _WithOptions:
    """Descriptor enabling task.with_options() on both the class and instances."""

    def __get__(self, obj, objtype=None):
        if obj is None:
            return self._as_decorator
        return obj._with_options

    @staticmethod
    def _as_decorator(
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
        auth_exempt: bool = False,
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
                auth_exempt=auth_exempt,
            )

        return wrapper


class task:
    with_options: _WithOptions = _WithOptions()

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
        auth_exempt: bool = False,
    ):
        self._func = func
        self.name = name if name else func.__name__
        self.description: str | None = None
        self.fallback = fallback
        self.rollback = rollback
        self.retry_max_attempts = retry_max_attempts
        self.retry_delay = retry_delay
        self.retry_backoff = retry_backoff
        self.timeout = timeout
        self.secret_requests = secret_requests
        self.output_storage = output_storage if output_storage else InlineOutputStorage()
        self.cache = cache
        self.metadata = metadata
        self.auth_exempt = auth_exempt
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

        if not self.auth_exempt:
            from flux.config import Configuration

            auth_config = Configuration.get().settings.security.auth
            if auth_config.enabled:
                import httpx

                from flux.security.errors import TaskAuthorizationError
                from flux.utils import get_logger as _get_logger

                exec_token = ctx.exec_token
                if not exec_token:
                    _get_logger(__name__).error(
                        f"Task '{full_name}': auth enabled but no exec_token on context — failing closed",
                    )
                    raise TaskAuthorizationError(
                        task_name=full_name,
                        task_id=task_id,
                        subject="unknown",
                        required_permission=f"workflow:{ctx.workflow_name}:task:{self.name}:execute",
                    )

                server_url = Configuration.get().settings.workers.server_url
                task_name_escaped = quote(self.name, safe="")
                authorize_url = (
                    f"{server_url}/executions/{ctx.execution_id}/authorize/{task_name_escaped}"
                )

                authorized = False
                try:
                    client = _get_auth_http_client()
                    resp = await client.post(
                        authorize_url,
                        headers={"Authorization": f"Bearer {exec_token}"},
                    )
                    if resp.status_code == 200:
                        try:
                            authorized = bool(resp.json().get("authorized", False))
                        except ValueError as _json_err:
                            _get_logger(__name__).error(
                                f"Task '{full_name}' authorize response not JSON "
                                f"(status={resp.status_code}, {len(resp.content)} bytes): "
                                f"{_json_err} — failing closed",
                            )
                except httpx.HTTPError as _auth_err:
                    _get_logger(__name__).error(
                        f"Task '{full_name}' authorization HTTP error: {_auth_err} — failing closed",
                    )

                if not authorized:
                    raise TaskAuthorizationError(
                        task_name=full_name,
                        task_id=task_id,
                        subject="unknown",
                        required_permission=f"workflow:{ctx.workflow_name}:task:{self.name}:execute",
                    )

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
            value = finished[0].value
            if isinstance(value, OutputStorageReference):
                reference = value
            else:
                if not isinstance(value, dict):
                    raise ExecutionError(
                        message=f"Failed to deserialize OutputStorageReference when replaying task '{full_name}' (task_id={task_id}). "
                        f"Expected OutputStorageReference or dict, but got {type(value)}: {value!r}.",
                    )
                try:
                    reference = OutputStorageReference.from_dict(value)
                except Exception as ex:
                    raise ExecutionError(
                        ex,
                        f"Failed to deserialize OutputStorageReference when replaying task '{full_name}' (task_id={task_id}). "
                        f"Original error: {ex}",
                    ) from ex
            return self.output_storage.retrieve(reference)

        from flux._task_context import _CURRENT_TASK

        task_token = _CURRENT_TASK.set((task_id, full_name))
        try:
            if not ctx.is_resuming and not ctx.has_resumed:
                ctx.events.append(
                    ExecutionEvent(
                        type=ExecutionEventType.TASK_STARTED,
                        source_id=task_id,
                        name=full_name,
                        value=task_args,
                    ),
                )

            import contextlib

            from flux.observability import get_metrics, is_enabled

            m = get_metrics()
            if m:
                m.record_task_started(ctx.workflow_name, self.name)

            task_failed = False
            task_start_time = time.monotonic()

            span_cm = contextlib.nullcontext()
            if is_enabled():
                from opentelemetry import trace as _trace

                tracer = _trace.get_tracer("flux")
                span_cm = tracer.start_as_current_span(
                    "flux.task.execute",
                    attributes={
                        "flux.task.name": full_name,
                        "flux.workflow.name": ctx.workflow_name,
                    },
                )

            with span_cm:
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
                    task_failed = True
                    output = await self.__handle_exception(
                        ctx,
                        ex,
                        task_id,
                        full_name,
                        task_args,
                        args,
                        kwargs,
                    )

            task_duration = time.monotonic() - task_start_time

            if m:
                status = "completed" if not task_failed else "failed"
                m.record_task_completed(ctx.workflow_name, self.name, status, task_duration)

            ctx.events.append(
                ExecutionEvent(
                    type=ExecutionEventType.TASK_COMPLETED,
                    source_id=task_id,
                    name=full_name,
                    value=self.output_storage.store(task_id, output),
                ),
            )

            await ctx.checkpoint()
            return output
        finally:
            _CURRENT_TASK.reset(task_token)

    @property
    def func(self) -> Callable:
        """The wrapped function."""
        return self._func

    def _with_options(
        self,
        name: str | None = None,
        fallback: Callable | None = None,
        rollback: Callable | None = None,
        retry_max_attempts: int | None = None,
        retry_delay: int | None = None,
        retry_backoff: int | None = None,
        timeout: int | None = None,
        secret_requests: list[str] | None = None,
        output_storage: OutputStorage | None = None,
        cache: bool | None = None,
        metadata: bool | None = None,
        auth_exempt: bool | None = None,
    ) -> task:
        """Return a new task with merged options. Values not provided inherit from this task."""
        return task(
            func=self._func,
            name=name if name is not None else self.name,
            fallback=fallback if fallback is not None else self.fallback,
            rollback=rollback if rollback is not None else self.rollback,
            retry_max_attempts=retry_max_attempts
            if retry_max_attempts is not None
            else self.retry_max_attempts,
            retry_delay=retry_delay if retry_delay is not None else self.retry_delay,
            retry_backoff=retry_backoff if retry_backoff is not None else self.retry_backoff,
            timeout=timeout if timeout is not None else self.timeout,
            secret_requests=secret_requests
            if secret_requests is not None
            else self.secret_requests,
            output_storage=output_storage if output_storage is not None else self.output_storage,
            cache=cache if cache is not None else self.cache,
            metadata=metadata if metadata is not None else self.metadata,
            auth_exempt=auth_exempt if auth_exempt is not None else self.auth_exempt,
        )

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
                if isinstance(ex, RetryError):
                    raise ExecutionError(ex)
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
                        value=self.output_storage.store(task_id, output),
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
                        value=self.output_storage.store(task_id, output),
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

            from flux.observability import get_metrics as _get_retry_metrics

            _m = _get_retry_metrics()
            if _m:
                _m.record_task_retry(ctx.workflow_name, self.name)

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
                            "output": self.output_storage.store(task_id, output),
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
