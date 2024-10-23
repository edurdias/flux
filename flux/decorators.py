from __future__ import annotations

import inspect
import os
import time
from concurrent.futures import ThreadPoolExecutor
from functools import wraps
from inspect import getfullargspec
from types import GeneratorType
from typing import Any
from typing import Callable
from typing import TypeVar

from flux.context import WorkflowExecutionContext
from flux.errors import ExecutionError
from flux.errors import RetryError
from flux.errors import WorkflowPausedError
from flux.events import ExecutionEvent
from flux.events import ExecutionEventType
from flux.executors import WorkflowExecutor
from flux.utils import call_with_timeout
from flux.utils import make_hashable

F = TypeVar("F", bound=Callable[..., Any])
END = "END"


class workflow:
    @staticmethod
    def is_workflow(func: F) -> bool:
        return func is not None and isinstance(func, workflow)

    def __init__(self, func: F):
        self._func = func
        self.name = func.__name__

    def __call__(self, *args) -> Any:
        if len(args) > 1 or not isinstance(args[0], WorkflowExecutionContext):
            raise TypeError(
                f"Expected first argument to be of type {type(WorkflowExecutionContext)}.",
            )

        ctx: WorkflowExecutionContext = args[0]

        qualified_name = f"{ctx.name}_{ctx.execution_id}"

        yield
        yield ExecutionEvent(
            ExecutionEventType.WORKFLOW_STARTED,
            qualified_name,
            ctx.name,
            ctx.input,
        )
        try:
            output = yield from (
                self._func(ctx) if self._func.__code__.co_argcount == 1 else self._func()
            )

            yield ExecutionEvent(
                ExecutionEventType.WORKFLOW_COMPLETED,
                qualified_name,
                ctx.name,
                output,
            )
        except WorkflowPausedError:
            pass
        except ExecutionError as ex:
            yield ExecutionEvent(
                ExecutionEventType.WORKFLOW_FAILED,
                qualified_name,
                ctx.name,
                ex.inner_exception,
            )
        except Exception as ex:
            # TODO: add retry support to workflow
            yield ExecutionEvent(
                ExecutionEventType.WORKFLOW_FAILED,
                qualified_name,
                ctx.name,
                ex,
            )

        yield END

    def run(
        self,
        input: Any | None = None,
        execution_id: str | None = None,
        options: dict[str, Any] = {},
    ) -> WorkflowExecutionContext:
        options.update({"module": self._func.__module__})
        return WorkflowExecutor.current(options).execute(
            self._func.__name__,
            input,
            execution_id,
        )

    def map(self, inputs: list[Any] = []) -> list[WorkflowExecutionContext]:
        with ThreadPoolExecutor(max_workers=os.cpu_count()) as executor:
            return list(executor.map(lambda i: self.run(i), inputs))


class task:
    @staticmethod
    def with_options(
        name: str | None = None,
        fallback: Callable | None = None,
        retry_max_attemps: int = 0,
        retry_delay: int = 1,
        retry_backoff: int = 2,
        timeout: int = 0,
        disable_replay: bool = False,
    ) -> Callable[[F], task]:
        def wrapper(func: F) -> task:
            return task(
                func=func,
                name=name,
                fallback=fallback,
                retry_max_attemps=retry_max_attemps,
                retry_delay=retry_delay,
                retry_backoff=retry_backoff,
                timeout=timeout,
                disable_replay=disable_replay,
            )

        return wrapper

    def __init__(
        self,
        func: F,
        name: str | None = None,
        fallback: Callable | None = None,
        retry_max_attemps: int = 0,
        retry_delay: int = 1,
        retry_backoff: int = 2,
        timeout: int = 0,
        disable_replay: bool = False,
    ):
        self._func = func
        self.name = name if not None else func.__name__
        self.fallback = fallback
        self.retry_max_attemps = retry_max_attemps
        self.retry_delay = retry_delay
        self.retry_backoff = retry_backoff
        self.timeout = timeout
        self.disable_replay = disable_replay
        wraps(func)(self)

    def __get__(self, instance, owner):
        return lambda *args, **kwargs: self(
            *(args if instance is None else (instance,) + args),
            **kwargs,
        )

    def __call__(self, *args, **kwargs) -> Any:
        task_args = self.__get_task_args(self._func, args)
        task_name = self.__get_task_name(self._func, self.name, task_args)
        task_args = {k: v for k, v in task_args.items() if k != "self"}
        task_id = self.__get_task_id(task_name, task_args, kwargs)

        yield ExecutionEvent(
            ExecutionEventType.TASK_STARTED,
            task_id,
            task_name,
            task_args,
        )

        output, replay = yield

        try:
            if replay:
                yield output

            output = call_with_timeout(
                lambda: self._func(*args, **kwargs),
                "Task",
                task_name,
                task_id,
                self.timeout,
            )

            if isinstance(output, GeneratorType):
                value = output
                while True:
                    value = yield value
                    value = output.send(value)

        except Exception as ex:
            if isinstance(ex, StopIteration):
                output = ex.value
            elif isinstance(ex, WorkflowPausedError):
                yield ExecutionEvent(
                    ExecutionEventType.TASK_COMPLETED,
                    task_id,
                    task_name,
                )
                yield ExecutionEvent(
                    ExecutionEventType.WORKFLOW_PAUSED,
                    task_id,
                    task_name,
                )
                raise
            elif self.retry_max_attemps > 0:
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
                            ExecutionEventType.TASK_RETRY_STARTED,
                            retry_task_id,
                            task_name,
                            retry_args,
                        )
                        output = self._func(*args, **kwargs)
                        yield ExecutionEvent(
                            ExecutionEventType.TASK_RETRY_COMPLETED,
                            retry_task_id,
                            task_name,
                            {
                                "current_attempt": attempt,
                                "max_attempts": self.retry_max_attemps,
                                "current_delay": current_delay,
                                "backoff": self.retry_backoff,
                                "output": output,
                            },
                        )
                        break
                    except Exception as e:
                        yield ExecutionEvent(
                            ExecutionEventType.TASK_RETRY_FAILED,
                            retry_task_id,
                            task_name,
                            {
                                "current_attempt": attempt,
                                "max_attempts": self.retry_max_attemps,
                                "current_delay": current_delay,
                                "backoff": self.retry_backoff,
                            },
                        )
                        if attempt == self.retry_max_attemps:
                            if self.fallback:
                                yield ExecutionEvent(
                                    ExecutionEventType.TASK_FALLBACK_STARTED,
                                    task_id,
                                    task_name,
                                    task_args,
                                )
                                output = self.fallback(*args, **kwargs)
                                yield ExecutionEvent(
                                    ExecutionEventType.TASK_FALLBACK_COMPLETED,
                                    task_id,
                                    task_name,
                                    output,
                                )
                            else:
                                raise RetryError(
                                    e,
                                    self.retry_max_attemps,
                                    self.retry_delay,
                                    self.retry_backoff,
                                )
            elif self.fallback:
                yield ExecutionEvent(
                    ExecutionEventType.TASK_FALLBACK_STARTED,
                    task_id,
                    task_name,
                    task_args,
                )
                output = self.fallback(*args, **kwargs)
                yield ExecutionEvent(
                    ExecutionEventType.TASK_FALLBACK_COMPLETED,
                    task_id,
                    task_name,
                    output,
                )
            else:
                yield ExecutionEvent(
                    ExecutionEventType.TASK_FAILED,
                    task_id,
                    task_name,
                    ex,
                )
                raise ExecutionError(ex)

        yield ExecutionEvent(
            ExecutionEventType.TASK_COMPLETED,
            task_id,
            task_name,
            output,
        )

        yield END

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
