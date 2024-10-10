import time

from typing import Callable
from string import Template
from functools import wraps
from types import GeneratorType
from inspect import getfullargspec

from flux.events import ExecutionEvent
from flux.events import ExecutionEventType
from flux.exceptions import ExecutionException, RetryException
from flux.utils import call_with_timeout


def task(
    fn: Callable = None,
    name: str = None,
    fallback: Callable = None,
    retry_max_attemps: int = 0,
    retry_delay: int = 1,
    retry_backoff: int = 2,
    timeout: int = 0,
):

    def _task(func: Callable):

        @wraps(func)
        def closure(*args, **kwargs):
            task_args = _get_task_args(func, args)
            task_name = _get_task_name(func, name, task_args)
            task_id = _get_task_id(task_name, args, kwargs)

            yield ExecutionEvent(
                ExecutionEventType.TASK_STARTED, task_id, task_name, task_args
            )

            output, replay = yield

            try:
                if not replay:

                    output = call_with_timeout(
                        lambda: func(*args, **kwargs),
                        "Task",
                        task_name,
                        task_id,
                        timeout,
                    )

                    if isinstance(output, GeneratorType):
                        output = yield output

            except Exception as ex:
                if isinstance(ex, StopIteration):
                    output = ex.value
                elif retry_max_attemps > 0:
                    attempt = 0
                    while attempt < retry_max_attemps:
                        attempt += 1
                        current_delay = retry_delay
                        try:
                            time.sleep(current_delay)
                            current_delay = min(current_delay * retry_backoff, 600)

                            yield ExecutionEvent(
                                ExecutionEventType.TASK_RETRY_STARTED,
                                task_id,
                                task_name,
                                {
                                    "current_attempt": attempt,
                                    "max_attempts": retry_max_attemps,
                                    "current_delay": current_delay,
                                    "backoff": retry_backoff,
                                },
                            )
                            output = func(*args, **kwargs)
                            yield ExecutionEvent(
                                ExecutionEventType.TASK_RETRY_COMPLETED,
                                task_id,
                                task_name,
                                {
                                    "current_attempt": attempt,
                                    "max_attempts": retry_max_attemps,
                                    "current_delay": current_delay,
                                    "backoff": retry_backoff,
                                    "output": output,
                                },
                            )
                            break
                        except Exception as e:
                            yield ExecutionEvent(
                                ExecutionEventType.TASK_RETRY_FAILED,
                                task_id,
                                task_name,
                                {
                                    "current_attempt": attempt,
                                    "max_attempts": retry_max_attemps,
                                    "current_delay": current_delay,
                                    "backoff": retry_backoff,
                                },
                            )
                            if attempt == retry_max_attemps:
                                if fallback:
                                    yield ExecutionEvent(
                                        ExecutionEventType.TASK_FALLBACK_STARTED,
                                        task_id,
                                        task_name,
                                        task_args,
                                    )
                                    output = fallback(*args, **kwargs)
                                    yield ExecutionEvent(
                                        ExecutionEventType.TASK_FALLBACK_COMPLETED,
                                        task_id,
                                        task_name,
                                        output,
                                    )
                                else:
                                    raise RetryException(
                                        e, retry_max_attemps, retry_delay, retry_backoff
                                    )
                elif fallback:
                    yield ExecutionEvent(
                        ExecutionEventType.TASK_FALLBACK_STARTED,
                        task_id,
                        task_name,
                        task_args,
                    )
                    output = fallback(*args, **kwargs)
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

        def _get_task_name(func: Callable, name: str, args: dict):
            task_name = f"{func.__name__}"
            if name is not None:
                task_name = Template(name).substitute(args)
            return task_name

        def _get_task_args(func: Callable, args: tuple):
            arg_names = getfullargspec(func).args
            arg_values = [
                v.__name__ if isinstance(v, Callable) else str(v) for v in args
            ]
            return dict(zip(arg_names, arg_values))

        def _get_task_id(task_name: str, args: tuple, kwargs: dict):
            return f"{task_name}_{abs(hash((task_name, args, tuple(sorted(kwargs.items())))))}"

        return closure

    return _task if fn is None else _task(fn)
