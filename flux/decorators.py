import inspect
import os
import time

from string import Template
from functools import wraps
from types import GeneratorType
from inspect import getfullargspec
from typing import Any, Callable, TypeVar
from concurrent.futures import ThreadPoolExecutor

from flux.events import ExecutionEvent
from flux.utils import call_with_timeout
from flux.catalogs import WorkflowCatalog
from flux.events import ExecutionEventType
from flux.executors import WorkflowExecutor
from flux.context_managers import ContextManager
from flux.context import WorkflowExecutionContext
from flux.exceptions import ExecutionException, RetryException

F = TypeVar("F", bound=Callable[..., Any])


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
                f"Expected first argument to be of type {type(WorkflowExecutionContext)}."
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
                self._func(ctx)
                if self._func.__code__.co_argcount == 1
                else self._func()
            )

            yield ExecutionEvent(
                ExecutionEventType.WORKFLOW_COMPLETED,
                qualified_name,
                ctx.name,
                output,
            )
        except ExecutionException as ex:
            yield ExecutionEvent(
                ExecutionEventType.WORKFLOW_FAILED,
                qualified_name,
                ctx.name,
                ex.inner_exception,
            )
        except Exception as ex:
            # TODO: add retry support to workflow
            raise

    def run(
        self, input: any = None, execution_id: str = None, options: dict[str, any] = []
    ) -> WorkflowExecutionContext:
        executor = WorkflowExecutor.current(options)
        return executor.execute(self._func.__name__, input, execution_id)

    def map(self, inputs: list[any] = []) -> list[WorkflowExecutionContext]:
        with ThreadPoolExecutor(max_workers=os.cpu_count()) as executor:
            return list(executor.map(lambda i: self.run(i), inputs))

    def __get_context_manager(self, context_manager: ContextManager) -> ContextManager:
        if context_manager:
            return context_manager
        return ContextManager.default()

    def __get_catalog(self, catalog: WorkflowCatalog = None) -> WorkflowCatalog:
        caller_globals = inspect.stack()[2].frame.f_globals
        if catalog:
            return catalog
        return WorkflowCatalog.create(caller_globals)


class task:

    @staticmethod
    def with_options(
        name: str = None,
        fallback: Callable = None,
        retry_max_attemps: int = 0,
        retry_delay: int = 1,
        retry_backoff: int = 2,
        timeout: int = 0,
    ) -> Callable[[F], "task"]:

        def wrapper(func: F) -> "task":
            return task(
                func,
                name,
                fallback,
                retry_max_attemps,
                retry_delay,
                retry_backoff,
                timeout,
            )

        return wrapper

    def __init__(
        self,
        func: F,
        name: str = None,
        fallback: Callable = None,
        retry_max_attemps: int = 0,
        retry_delay: int = 1,
        retry_backoff: int = 2,
        timeout: int = 0,
    ):
        self._func = func
        self.name = name if not None else func.__name__
        self.fallback = fallback
        self.retry_max_attemps = retry_max_attemps
        self.retry_delay = retry_delay
        self.retry_backoff = retry_backoff
        self.timeout = timeout
        wraps(func)(self)

    def __call__(self, *args, **kwargs) -> Any:
        task_args = self.__get_task_args(self._func, args)
        task_name = self.__get_task_name(self._func, self.name, task_args)
        task_id = self.__get_task_id(task_name, task_args, kwargs)

        yield ExecutionEvent(
            ExecutionEventType.TASK_STARTED, task_id, task_name, task_args
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
                nested_output = yield output  # send for processing
                output.send(nested_output)

        except Exception as ex:
            if isinstance(ex, StopIteration):
                output = ex.value
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
                        task_id, task_args, {**kwargs, **retry_args}
                    )

                    try:
                        time.sleep(current_delay)
                        current_delay = min(current_delay * self.retry_backoff, 600)

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
                                raise RetryException(
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
                    ExecutionEventType.TASK_FAILED, task_id, task_name, ex
                )
                raise ExecutionException(ex)

        yield ExecutionEvent(
            ExecutionEventType.TASK_COMPLETED, task_id, task_name, output
        )

    def map(self, args: list[any] = []):
        with ThreadPoolExecutor(max_workers=os.cpu_count()) as executor:
            return list(
                executor.map(
                    lambda arg: self(*arg) if isinstance(arg, list) else self(arg), args
                )
            )

    def __get_task_name(self, func: Callable, name: str, args: dict):
        task_name = f"{func.__name__}"
        if name is not None:
            task_name = Template(name).substitute(args)
        return task_name

    def __get_task_args(self, func: Callable, args: tuple):
        arg_names = getfullargspec(func).args
        arg_values = []
        for arg in args:
            if isinstance(arg, workflow):
                arg_values.append(arg.name)
            elif isinstance(arg, Callable):
                arg_values.append(arg.__name__)
            elif isinstance(arg, list):
                arg_values.append(tuple(arg))
            else:
                arg_values.append(arg)

        return dict(zip(arg_names, arg_values))

    def __get_task_id(self, task_name: str, args: dict, kwargs: dict):
        return f"{task_name}_{abs(hash((task_name, tuple(sorted(args.items())), tuple(sorted(kwargs.items())))))}"
