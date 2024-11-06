from __future__ import annotations

import inspect
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import wraps
from inspect import getfullargspec
from typing import Any
from typing import Callable
from typing import Generic
from typing import TypeVar

from flux.context import WorkflowExecutionContext
from flux.errors import ExecutionError
from flux.errors import RetryError
from flux.events import ExecutionEvent
from flux.events import ExecutionEventType
from flux.executors import WorkflowExecutor
from flux.output_storage import OutputStorage
from flux.secret_managers import SecretManager
from flux.utils import call_with_timeout
from flux.utils import make_hashable

T = TypeVar("T", bound=Any)
F = TypeVar("F", bound=Callable[..., Any])
END = "END"


@dataclass
class PauseRequested(Generic[T]):
    reference: str
    input_type: type[T] | None


def pause(reference: str, wait_for_input: type[T] | None = None):
    return PauseRequested(reference, wait_for_input)


class workflow:
    @staticmethod
    def is_workflow(func: F) -> bool:
        return func is not None and isinstance(func, workflow)

    @staticmethod
    def with_options(
        name: str | None = None,
        secret_requests: list[str] = [],
        output_storage: OutputStorage | None = None,
    ) -> Callable[[F], workflow]:
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

    def __get__(self, instance, owner):
        return lambda *args, **kwargs: self(
            *(args if instance is None else (instance,) + args),
            **kwargs,
        )

    def __call__(self, *args) -> Any:
        if len(args) > 1 or not isinstance(args[0], WorkflowExecutionContext):
            raise TypeError(
                f"Expected first argument to be of type {type(WorkflowExecutionContext)}.",
            )

        ctx: WorkflowExecutionContext = args[0]

        self.id = f"{ctx.name}_{ctx.execution_id}"

        yield self

        yield ExecutionEvent(
            ExecutionEventType.WORKFLOW_STARTED,
            self.id,
            ctx.name,
            ctx.input,
        )
        try:
            output = yield from (
                self._func(ctx) if self._func.__code__.co_argcount == 1 else self._func()
            )

            yield ExecutionEvent(
                type=ExecutionEventType.WORKFLOW_COMPLETED,
                source_id=self.id,
                name=ctx.name,
                value=self.output_storage.store(self.id, output) if self.output_storage else output,
            )
        except ExecutionError as ex:
            yield ExecutionEvent(
                type=ExecutionEventType.WORKFLOW_FAILED,
                source_id=self.id,
                name=ctx.name,
                value=ex.inner_exception,
            )
        except Exception as ex:
            # TODO: add retry support to workflow
            yield ExecutionEvent(
                type=ExecutionEventType.WORKFLOW_FAILED,
                source_id=self.id,
                name=ctx.name,
                value=ex,
            )

        yield END

    def run(
        self,
        input: Any | None = None,
        execution_id: str | None = None,
        options: dict[str, Any] = {},
    ) -> WorkflowExecutionContext:
        options.update({"module": self._func.__module__})
        return WorkflowExecutor.get(options).execute(self._func.__name__, input, execution_id)

    def map(self, inputs: list[Any] = []) -> list[WorkflowExecutionContext]:
        with ThreadPoolExecutor(max_workers=os.cpu_count()) as executor:
            return list(executor.map(lambda i: self.run(i), inputs))


@dataclass
class TaskContext:
    id: str
    name: str
    full_name: str


class task:
    @staticmethod
    def with_options(
        name: str | None = None,
        fallback: Callable | None = None,
        rollback: Callable | None = None,
        retry_max_attemps: int = 0,
        retry_delay: int = 1,
        retry_backoff: int = 2,
        timeout: int = 0,
        secret_requests: list[str] = [],
        output_storage: OutputStorage | None = None,
    ) -> Callable[[F], task]:
        def wrapper(func: F) -> task:
            return task(
                func=func,
                name=name,
                fallback=fallback,
                rollback=rollback,
                retry_max_attemps=retry_max_attemps,
                retry_delay=retry_delay,
                retry_backoff=retry_backoff,
                timeout=timeout,
                secret_requests=secret_requests,
                output_storage=output_storage,
            )

        return wrapper

    def __init__(
        self,
        func: F,
        name: str | None = None,
        fallback: Callable | None = None,
        rollback: Callable | None = None,
        retry_max_attemps: int = 0,
        retry_delay: int = 1,
        retry_backoff: int = 2,
        timeout: int = 0,
        secret_requests: list[str] = [],
        output_storage: OutputStorage | None = None,
    ):
        self._func = func
        self.name = name if not None else func.__name__
        self.fallback = fallback
        self.rollback = rollback
        self.retry_max_attemps = retry_max_attemps
        self.retry_delay = retry_delay
        self.retry_backoff = retry_backoff
        self.timeout = timeout
        self.secret_requests = secret_requests
        self.output_storage = output_storage
        wraps(func)(self)

    def __get__(self, instance, owner):
        return lambda *args, **kwargs: self(
            *(args if instance is None else (instance,) + args),
            **kwargs,
        )

    def __call__(self, *args, **kwargs) -> Any:
        args_with_self = self.__get_task_args(self._func, args)
        self.full_name = self.__get_task_name(self._func, self.name, args_with_self)

        task_args = {k: v for k, v in args_with_self.items() if k != "self"}
        self.task_id = self.__get_task_id(self.full_name, task_args, kwargs)

        yield ExecutionEvent(
            type=ExecutionEventType.TASK_STARTED,
            source_id=self.task_id,
            name=self.full_name,
            value=task_args,
        )

        try:
            output = yield from self.__execute(self.task_id, self.full_name, args, kwargs)
        except Exception as ex:
            output = yield from self.__handle_exception(ex, task_args, args, kwargs)

        output = yield output

        yield ExecutionEvent(
            type=ExecutionEventType.TASK_COMPLETED,
            source_id=self.task_id,
            name=self.full_name,
            value=self.output_storage.store(self.task_id, output)
            if self.output_storage
            else output,
        )

        return output

    def map(self, args: list[Any] = []):
        with ThreadPoolExecutor(max_workers=os.cpu_count()) as executor:
            return list(
                executor.map(
                    lambda arg: (
                        self(*arg)
                        if isinstance(
                            arg,
                            list,
                        )
                        else self(arg)
                    ),
                    args,
                ),
            )

    def __get_task_name(self, func: Callable, name: str | None, args: dict) -> str:
        return name.format(**args) if name else f"{func.__name__}"

    def __get_task_args(self, func: Callable, args: tuple) -> dict:
        arg_names = getfullargspec(func).args
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

    def __get_task_id(self, task_name: str, args: dict, kwargs: dict):
        return f"{task_name}_{abs(hash((task_name, make_hashable(args), make_hashable(kwargs))))}"

    def __execute(
        self,
        task_id: str,
        task_name: str,
        args: tuple,
        kwargs: dict,
    ):
        output_replay = yield
        output, replay = output_replay if output_replay else (None, False)
        if replay:
            return output

        yield

        if self.secret_requests:
            secrets = SecretManager.current().get(self.secret_requests)
            kwargs = {**kwargs, "secrets": secrets}

        output = call_with_timeout(
            lambda: self._func(*args, **kwargs),
            "Task",
            task_name,
            task_id,
            self.timeout,
        )

        # if isinstance(output, GeneratorType):
        #     try:
        #         value = output
        #         while value is not None:
        #             value = yield value
        #             value = output.send(value)
        #     except StopIteration:
        #         output = value
        return output

    def __handle_exception(
        self,
        ex: Exception,
        task_args: dict,
        args: tuple,
        kwargs: dict,
        retry_attempts: int = 0,
    ):
        try:
            if isinstance(ex, StopIteration):
                return ex.value
            elif self.retry_max_attemps > 0 and retry_attempts < self.retry_max_attemps:
                return (
                    yield from self.__handle_retry(
                        self.task_id,
                        self.full_name,
                        task_args,
                        args,
                        kwargs,
                    )
                )
            elif self.fallback:
                return (
                    yield from self.__handle_fallback(
                        self.task_id,
                        self.full_name,
                        task_args,
                        args,
                        kwargs,
                    )
                )
            else:
                if self.rollback:
                    yield from self.__handle_rollback(
                        self.task_id,
                        self.full_name,
                        task_args,
                        args,
                        kwargs,
                    )

                yield ExecutionEvent(
                    type=ExecutionEventType.TASK_FAILED,
                    source_id=self.task_id,
                    name=self.full_name,
                    value=ex,
                )
                raise ExecutionError(ex)

        except RetryError as rex:
            output = yield from self.__handle_exception(
                rex,
                task_args,
                args,
                kwargs,
                retry_attempts=rex.retry_attempts,
            )
            return output

    def __handle_fallback(
        self,
        task_id: str,
        task_name: str,
        task_args: dict,
        args: tuple,
        kwargs: dict,
    ):
        if self.fallback:
            yield ExecutionEvent(
                type=ExecutionEventType.TASK_FALLBACK_STARTED,
                source_id=task_id,
                name=task_name,
                value=task_args,
            )
            try:
                output = self.fallback(*args, **kwargs)
                yield ExecutionEvent(
                    type=ExecutionEventType.TASK_FALLBACK_COMPLETED,
                    source_id=task_id,
                    name=task_name,
                    value=self.output_storage.store(self.task_id, output)
                    if self.output_storage
                    else output,
                )
            except Exception as ex:
                yield ExecutionEvent(
                    type=ExecutionEventType.TASK_FALLBACK_FAILED,
                    source_id=task_id,
                    name=task_name,
                    value=ex,
                )
                if isinstance(ex, ExecutionError):
                    raise ex
                raise ExecutionError(ex)

            return output

    def __handle_rollback(
        self,
        task_id: str,
        task_name: str,
        task_args: dict,
        args: tuple,
        kwargs: dict,
    ):
        if self.rollback:
            yield ExecutionEvent(
                type=ExecutionEventType.TASK_ROLLBACK_STARTED,
                source_id=task_id,
                name=task_name,
                value=task_args,
            )
            try:
                output = self.rollback(*args, **kwargs)
                yield ExecutionEvent(
                    type=ExecutionEventType.TASK_ROLLBACK_COMPLETED,
                    source_id=task_id,
                    name=task_name,
                    value=self.output_storage.store(self.task_id, output)
                    if self.output_storage
                    else output,
                )
            except Exception as ex:
                yield ExecutionEvent(
                    type=ExecutionEventType.TASK_ROLLBACK_FAILED,
                    source_id=task_id,
                    name=task_name,
                    value=ex,
                )

    def __handle_retry(
        self,
        task_id: str,
        task_name: str,
        task_args: dict,
        args: tuple,
        kwargs: dict,
    ):
        attempt = 0
        while attempt < self.retry_max_attemps:
            attempt += 1
            current_delay = self.retry_delay
            retry_args = {
                "current_attempt": attempt,
                "max_attempts": self.retry_max_attemps,
                "current_delay": current_delay,
                "backoff": self.retry_backoff,
            }

            retry_task_id = self.__get_task_id(
                task_id,
                task_args,
                {**kwargs, **retry_args},
            )

            try:
                time.sleep(current_delay)
                current_delay = min(
                    current_delay * self.retry_backoff,
                    600,
                )

                yield ExecutionEvent(
                    type=ExecutionEventType.TASK_RETRY_STARTED,
                    source_id=retry_task_id,
                    name=task_name,
                    value=retry_args,
                )
                output = yield from self.__execute(task_id, task_name, args, kwargs)
                yield ExecutionEvent(
                    type=ExecutionEventType.TASK_RETRY_COMPLETED,
                    source_id=retry_task_id,
                    name=task_name,
                    value={
                        "current_attempt": attempt,
                        "max_attempts": self.retry_max_attemps,
                        "current_delay": current_delay,
                        "backoff": self.retry_backoff,
                        "output": self.output_storage.store(self.task_id, output)
                        if self.output_storage
                        else output,
                    },
                )
                return output
            except Exception as ex:
                yield ExecutionEvent(
                    type=ExecutionEventType.TASK_RETRY_FAILED,
                    source_id=retry_task_id,
                    name=task_name,
                    value={
                        "current_attempt": attempt,
                        "max_attempts": self.retry_max_attemps,
                        "current_delay": current_delay,
                        "backoff": self.retry_backoff,
                    },
                )
                if attempt == self.retry_max_attemps:
                    raise RetryError(
                        ex,
                        self.retry_max_attemps,
                        self.retry_delay,
                        self.retry_backoff,
                    )
