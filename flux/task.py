import time
from typing import Callable
from functools import wraps
from inspect import getfullargspec
from string import Template

from flux.events import ExecutionEvent
from flux.events import ExecutionEventType
from flux.exceptions import ExecutionException, RetryException


# TODO: add timeout
def task(
    fn: Callable = None,
    name: str = None,
    fallback: Callable = None,
    retry_max_attemps: int = 0,
    retry_delay: int = 1,
    retry_backoff: int = 2,
):

    def _task(func: Callable):

        @wraps(func)
        def closure(*args, **kwargs):
            task_name = _get_task_name(args)
            task_id = _get_task_id(task_name, args, kwargs)

            yield ExecutionEvent(
                ExecutionEventType.TASK_STARTED, task_id, task_name, args
            )
            output, replay = yield

            try:
                if not replay:
                    output = func(*args, **kwargs)
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
                                },
                            )
                            break
                        except Exception as e:
                            if attempt == retry_max_attemps:
                                if fallback:
                                    yield ExecutionEvent(
                                        ExecutionEventType.TASK_FALLBACK_STARTED,
                                        task_id,
                                        task_name,
                                    )
                                    output = fallback(*args, **kwargs)
                                    yield ExecutionEvent(
                                        ExecutionEventType.TASK_FALLBACK_COMPLETED,
                                        task_id,
                                        task_name,
                                    )
                                else:
                                    raise RetryException(
                                        e, retry_max_attemps, retry_delay, retry_backoff
                                    )
                elif fallback:
                    yield ExecutionEvent(
                        ExecutionEventType.TASK_FALLBACK_STARTED, task_id, task_name
                    )
                    output = fallback(*args, **kwargs)
                    yield ExecutionEvent(
                        ExecutionEventType.TASK_FALLBACK_COMPLETED, task_id, task_name
                    )
                else:
                    raise ExecutionException(ex)

            yield ExecutionEvent(
                ExecutionEventType.TASK_COMPLETED, task_id, task_name, output
            )

        def _get_task_name(args):
            task_name = f"{func.__name__}"
            if name is not None:
                arg_names = getfullargspec(func).args
                map = dict(zip(arg_names, args))
                task_name = Template(name).substitute(map)
            return task_name

        def _get_task_id(task_name, args, kwargs):
            return f"{task_name}_{abs(hash((task_name, args, tuple(sorted(kwargs.items())))))}"

        return closure

    return _task if fn is None else _task(fn)