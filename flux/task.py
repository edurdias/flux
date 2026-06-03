from __future__ import annotations

from flux import ExecutionContext
from flux.cache import CacheManager
from flux.domain.events import ExecutionEvent, ExecutionEventType
from flux.errors import ExecutionError, ExecutionTimeoutError, PauseRequested, RetryError
from flux.output_storage import InlineOutputStorage, OutputStorage, OutputStorageReference
from flux.secret_managers import SecretManager
from flux.utils import get_func_args, make_deterministic, maybe_awaitable


import asyncio
import hashlib
import inspect
import json
import time
from functools import wraps
from typing import Any, TypeVar
from collections.abc import Awaitable, Callable
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
        secret_requests: list[str] | None = None,
        config_requests: list[str] | None = None,
        output_storage: OutputStorage | None = None,
        cache: bool = False,
        metadata: bool = False,
        auth_exempt: bool = False,
        requires_approval: bool | Callable[..., bool | Awaitable[bool]] = False,
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
                secret_requests=list(secret_requests) if secret_requests else [],
                config_requests=list(config_requests) if config_requests else [],
                output_storage=output_storage,
                cache=cache,
                metadata=metadata,
                auth_exempt=auth_exempt,
                requires_approval=requires_approval,
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
        secret_requests: list[str] | None = None,
        config_requests: list[str] | None = None,
        output_storage: OutputStorage | None = None,
        cache: bool = False,
        metadata: bool = False,
        auth_exempt: bool = False,
        requires_approval: bool | Callable[..., bool | Awaitable[bool]] = False,
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
        self.secret_requests = list(secret_requests) if secret_requests else []
        self.config_requests = list(config_requests) if config_requests else []
        self.output_storage = output_storage if output_storage else InlineOutputStorage()
        self.cache = cache
        self.metadata = metadata
        self.auth_exempt = auth_exempt
        self.requires_approval = requires_approval
        wraps(func)(self)

    def __get__(self, instance, owner):
        return lambda *args, **kwargs: self(
            *(args if instance is None else (instance,) + args),
            **kwargs,
        )

    @staticmethod
    def _compute_task_id(full_name: str, task_args: dict, args: tuple, kwargs: dict) -> str:
        """Deterministic, cross-process-stable id for a task call.

        This is the source_id the replay short-circuit matches on and the
        approval gate's call_id, so it must be identical in every process. SHA256
        over a canonical form — builtin hash() is per-process randomized (the
        same defect was already fixed for event ids; see flux/domain/events.py).
        """
        canonical = json.dumps(
            make_deterministic([full_name, task_args, args, kwargs]),
            sort_keys=True,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
        return f"{full_name}_{digest}"

    async def __call__(self, *args, **kwargs) -> Any:
        task_args = get_func_args(self._func, args)
        full_name = self.name.format(**task_args)

        task_id = self._compute_task_id(full_name, task_args, args, kwargs)

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
                        required_permission=(
                            f"workflow:{ctx.workflow_namespace}:{ctx.workflow_name}"
                            f":task:{self.name}:execute"
                        ),
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
                        required_permission=(
                            f"workflow:{ctx.workflow_namespace}:{ctx.workflow_name}"
                            f":task:{self.name}:execute"
                        ),
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
            event = finished[0]
            value = event.value
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
            retrieved = self.output_storage.retrieve(reference)
            if event.type == ExecutionEventType.TASK_FAILED:
                # Persisted failure — re-raise the stored exception so the
                # workflow body sees the same exception it saw on the
                # original run. Without this branch the replay path would
                # return the exception object as if it were a successful
                # output.
                raise retrieved
            return retrieved

        # Approval gate — evaluated after auth and the replay short-circuit.
        # ``task_id`` doubles as the approval ``task_call_id`` for the
        # initial call; each retry attempt is gated under its own id.
        gate_resumed = await self._approval_gate(ctx, task_id, full_name, args, kwargs)

        from flux._task_context import _CURRENT_TASK

        task_token = _CURRENT_TASK.set((task_id, full_name))
        try:
            if gate_resumed or (not ctx.is_resuming and not ctx.has_resumed):
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
                m.record_task_started(ctx.workflow_namespace, ctx.workflow_name, self.name)

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

            with span_cm as span:
                try:
                    output = None
                    cache_hit = False
                    if self.cache:
                        output = CacheManager.get(task_id)
                        cache_hit = output is not None

                    if not cache_hit:
                        if self.secret_requests:
                            secrets = await SecretManager.current().get(self.secret_requests)
                            kwargs = {**kwargs, "secrets": secrets}

                        if self.config_requests:
                            resolved_keys = [k.format(**task_args) for k in self.config_requests]
                            from flux.config_manager import ConfigManager

                            configs = await ConfigManager.current().get(resolved_keys)
                            kwargs = {**kwargs, "config": configs}

                        if self.metadata:
                            kwargs = {**kwargs, "metadata": TaskMetadata(task_id, full_name)}

                        if self.timeout > 0:
                            try:
                                output = await asyncio.wait_for(
                                    maybe_awaitable(self._func(*args, **kwargs)),
                                    timeout=self.timeout,
                                )
                            except TimeoutError as ex:
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
                    if span is not None:
                        from opentelemetry.trace import Status, StatusCode

                        span.set_status(Status(StatusCode.ERROR, str(ex)))
                        span.record_exception(ex)
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
                m.record_task_completed(
                    ctx.workflow_namespace,
                    ctx.workflow_name,
                    self.name,
                    status,
                    task_duration,
                )

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
        config_requests: list[str] | None = None,
        output_storage: OutputStorage | None = None,
        cache: bool | None = None,
        metadata: bool | None = None,
        auth_exempt: bool | None = None,
        requires_approval: bool | Callable[..., bool | Awaitable[bool]] | None = None,
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
            config_requests=config_requests
            if config_requests is not None
            else self.config_requests,
            output_storage=output_storage if output_storage is not None else self.output_storage,
            cache=cache if cache is not None else self.cache,
            metadata=metadata if metadata is not None else self.metadata,
            auth_exempt=auth_exempt if auth_exempt is not None else self.auth_exempt,
            requires_approval=(
                requires_approval if requires_approval is not None else self.requires_approval
            ),
        )

    async def _evaluate_approval_predicate(self, args: tuple, kwargs: dict) -> bool:
        """Evaluate the requires_approval spec for this call.

        Static True/False short-circuit. Callables are bound against the wrapped
        function's signature so the predicate sees the same parameter names the
        task body does. Async callables are awaited.
        """
        spec = self.requires_approval
        if spec is True:
            return True
        if spec is False or spec is None:
            return False
        # Only argument *binding* may fall back to raw args — a TypeError from
        # the predicate body itself must propagate, not trigger a second
        # invocation that could duplicate side effects.
        try:
            bound = inspect.signature(self._func).bind(*args, **kwargs)
            bound.apply_defaults()
        except TypeError:
            call_args, call_kwargs = args, kwargs
        else:
            call_args, call_kwargs = bound.args, bound.kwargs
        result = spec(*call_args, **call_kwargs)
        if inspect.isawaitable(result):
            result = await result
        return bool(result)

    async def _approval_gate(
        self,
        ctx: ExecutionContext,
        call_id: str,
        full_name: str,
        args: tuple,
        kwargs: dict,
    ) -> bool:
        """Run the approval gate for one task attempt identified by ``call_id``.

        Returns ``True`` when the gate consumed a resume transition for this
        task (an approval decided while the execution was suspended), so the
        caller knows to still emit ``TASK_STARTED`` for the gated task even
        though ``ctx`` is no longer ``is_resuming``. Returns ``False`` when
        approved without resuming or when no approval is required. Raises
        ``ApprovalRejected`` on rejection, ``asyncio.CancelledError`` on
        cancellation, and ``PauseRequested`` (via ``ctx._await_approval``)
        while waiting for a decision. An unexpected gate failure — a
        predicate that raises, or an approval-store error — is recorded as a
        ``TASK_FAILED`` event and re-raised as an ``ExecutionError``.

        Determinism: the approval row keyed by ``call_id`` is the durable
        record that this gate was triggered. It is looked up *before* the
        predicate runs so replay honors the original verdict regardless of
        whether the predicate is non-deterministic. The initial call uses
        ``task_id`` as ``call_id``; each retry attempt uses a distinct id so
        every attempt is independently re-gated.
        """
        if self.requires_approval is False:
            return False

        from flux.approvals import ApprovalManager, ApprovalRejected

        try:
            mgr = ApprovalManager()
            existing = mgr.get_by_call(ctx.execution_id, call_id)

            if existing is not None:
                verdict_required = True
            else:
                verdict_required = await self._evaluate_approval_predicate(args, kwargs)

            if not verdict_required:
                return False

            if existing is None:
                from flux.context_managers import ContextManager
                from flux.unit_of_work import UnitOfWork

                cm = ContextManager.create()
                with UnitOfWork() as uow:
                    ctx.events.append(
                        ExecutionEvent(
                            type=ExecutionEventType.TASK_AWAITING_APPROVAL,
                            source_id=call_id,
                            name=full_name,
                            value={
                                "task_call_id": call_id,
                                "workflow_namespace": ctx.workflow_namespace,
                                "workflow_name": ctx.workflow_name,
                                "task_name": self.name,
                            },
                        ),
                    )
                    # Persist the execution context *before* the approval row
                    # is flushed so the approval_requests.execution_id foreign
                    # key has its target row (enforced on PostgreSQL).
                    cm.save(ctx, uow=uow)
                    mgr.create(
                        execution_id=ctx.execution_id,
                        task_call_id=call_id,
                        workflow_namespace=ctx.workflow_namespace,
                        workflow_name=ctx.workflow_name,
                        task_name=self.name,
                        uow=uow,
                    )
                    uow.commit()

            verdict = ctx._await_approval(call_id)
        except (PauseRequested, asyncio.CancelledError):
            # Expected suspension / cancellation signals — propagate untouched.
            raise
        except ApprovalRejected as ex:
            # Rejection is a terminal task failure that deliberately bypasses
            # retry/fallback/rollback. If the rejection arrived on a resumed
            # execution, consume the resume transition first so workflow code
            # that catches ``ApprovalRejected`` continues from RUNNING rather
            # than a stuck RESUME_CLAIMED state.
            if ctx.is_resuming:
                ctx.resume()
            await self._record_gate_failure(ctx, call_id, full_name, ex)
            raise
        except Exception as ex:
            # An unexpected gate failure — a requires_approval predicate that
            # raised, or an approval-store error. The docs say predicate
            # exceptions fail the task call, so route it through the same
            # TASK_FAILED path instead of letting it escape the engine
            # unrecorded.
            wrapped = ex if isinstance(ex, ExecutionError) else ExecutionError(ex)
            await self._record_gate_failure(ctx, call_id, full_name, wrapped)
            raise wrapped

        if verdict.cancelled:
            raise asyncio.CancelledError()

        # Approved. If this gate is the point a paused workflow resumed from,
        # transition the context back to RUNNING — mirroring the pause()
        # primitive — so subsequent task calls no longer see the execution as
        # resuming and emit their TASK_STARTED events.
        if ctx.is_resuming:
            ctx.resume()
            return True
        return False

    async def _record_gate_failure(
        self,
        ctx: ExecutionContext,
        call_id: str,
        full_name: str,
        error: BaseException,
    ) -> None:
        """Persist a ``TASK_FAILED`` event for an approval-gate failure.

        Idempotent: a replayed retry gate that re-derives the failure from the
        approval row must not append a duplicate event.
        """
        already_failed = any(
            e.source_id == call_id and e.type == ExecutionEventType.TASK_FAILED for e in ctx.events
        )
        if already_failed:
            return
        ctx.events.append(
            ExecutionEvent(
                type=ExecutionEventType.TASK_FAILED,
                source_id=call_id,
                name=full_name,
                value=self.output_storage.store(call_id, error),
            ),
        )
        await ctx.checkpoint()

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

        from flux.approvals import ApprovalRejected

        if isinstance(ex, ApprovalRejected):
            raise

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

                # Compute the final exception once, store *that* via
                # output_storage, then raise the same instance. Storing the
                # wrapped exception (rather than the raw `ex`) keeps replay
                # symmetric: the workflow body sees the same exception type
                # on the original run and on every replay.
                # RetryError is a subclass of ExecutionError but is wrapped
                # again here to preserve the prior identity contract — the
                # workflow body sees ExecutionError(RetryError(...)), not
                # bare RetryError.
                if isinstance(ex, RetryError):
                    final = ExecutionError(ex)
                elif isinstance(ex, ExecutionError):
                    final = ex
                else:
                    final = ExecutionError(ex)
                ctx.events.append(
                    ExecutionEvent(
                        type=ExecutionEventType.TASK_FAILED,
                        source_id=task_id,
                        name=task_full_name,
                        value=self.output_storage.store(task_id, final),
                    ),
                )
                raise final

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
        # current_delay persists across iterations so retry_backoff actually
        # compounds: attempt 1 waits retry_delay, attempt 2 retry_delay*backoff, …
        current_delay = self.retry_delay
        while attempt < self.retry_max_attempts:
            attempt += 1

            # Re-gate every retry attempt under its own approval id so each
            # attempt is independently reconsidered. Raised PauseRequested /
            # ApprovalRejected must propagate past the retry loop, so the
            # gate is invoked outside the per-attempt try/except below.
            await self._approval_gate(
                ctx,
                f"{task_id}~retry{attempt}",
                task_full_name,
                args,
                kwargs,
            )

            from flux.observability import get_metrics as _get_retry_metrics

            _m = _get_retry_metrics()
            if _m:
                _m.record_task_retry(ctx.workflow_namespace, ctx.workflow_name, self.name)

            retry_args = {
                "current_attempt": attempt,
                "max_attempts": self.retry_max_attempts,
                "current_delay": current_delay,
                "backoff": self.retry_backoff,
            }

            try:
                await asyncio.sleep(current_delay)

                ctx.events.append(
                    ExecutionEvent(
                        type=ExecutionEventType.TASK_RETRY_STARTED,
                        source_id=task_id,
                        name=task_full_name,
                        value=retry_args,
                    ),
                )
                if self.timeout > 0:
                    try:
                        output = await asyncio.wait_for(
                            maybe_awaitable(self._func(*args, **kwargs)),
                            timeout=self.timeout,
                        )
                    except TimeoutError as ex:
                        raise ExecutionTimeoutError(
                            "Task",
                            self.name,
                            task_id,
                            self.timeout,
                        ) from ex
                else:
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
                current_delay = min(current_delay * self.retry_backoff, 600)
