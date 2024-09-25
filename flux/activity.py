from flux.events import ExecutionEvent
from flux.events import ExecutionEventType
from flux.exceptions import ExecutionException
from flux.exceptions import RetryException


import time
from typing import Callable


def activity(fn: Callable = None, retry_attemps: int = 0, retry_delay: int = 0):

    def _activity(func: Callable):
        current_attempt = 1

        def closure(*args, **kwargs):
            name = f"{func.__name__}"

            def retry(ex: Exception):
                nonlocal current_attempt
                try:
                    time.sleep(retry_delay)
                    output = func(*args, **kwargs)
                    return output
                except Exception as ex:
                    if current_attempt <= retry_attemps:
                        current_attempt = current_attempt + 1
                        return retry(ex)
                    else:
                        raise RetryException(ex, retry_attemps, retry_delay)

            yield ExecutionEvent(ExecutionEventType.ACTIVITY_STARTED, name, args)
            output, replay = yield

            try:
                output = (
                    output
                    if replay is True
                    else func(
                        *args,
                    )
                )

            except Exception as ex:
                if current_attempt <= retry_attemps:
                    output = retry(ex)
                else:
                    raise ExecutionException(ex)

            yield ExecutionEvent(ExecutionEventType.ACTIVITY_COMPLETED, name, output)
            return output

        return closure

    return _activity if fn is None else _activity(fn)