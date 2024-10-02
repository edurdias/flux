import time
from typing import Callable
from functools import wraps
from inspect import getfullargspec
from string import Template

from flux.events import ExecutionEvent
from flux.events import ExecutionEventType
from flux.exceptions import ExecutionException, RetryException


def activity(fn: Callable = None, name:str = None, retry_max_attemps: int = 0, retry_delay: int = 1, retry_backoff: int = 2):

    def _activity(func: Callable):
        
        @wraps(func)
        def closure(*args, **kwargs):
            activity_name = _get_activity_name(args)
            activity_id = _get_activity_id(activity_name, args, kwargs)

            yield ExecutionEvent(ExecutionEventType.ACTIVITY_STARTED, activity_id, activity_name, args)
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
                            
                            yield ExecutionEvent(ExecutionEventType.ACTIVITY_RETRIED, activity_id, activity_name, {
                                "current_attempt": attempt,
                                "max_attempts": retry_max_attemps,
                                "current_delay": current_delay,
                                "backoff": retry_backoff
                            })
                            output = func(*args, **kwargs)
                            break
                        except Exception as e:
                            if attempt == retry_max_attemps:
                                raise RetryException(e, retry_max_attemps, retry_delay, retry_backoff)
                else:
                    raise ExecutionException(ex)

            yield ExecutionEvent(ExecutionEventType.ACTIVITY_COMPLETED, activity_id, activity_name, output)

        def _get_activity_name(args):
            activity_name = f"{func.__name__}" 
            if name is not None:
                arg_names = getfullargspec(func).args
                map = dict(zip(arg_names, args))
                activity_name = Template(name).substitute(map)
            return activity_name

        def _get_activity_id(activity_name, args, kwargs):
            return f"{activity_name}_{abs(hash((activity_name, args, tuple(sorted(kwargs.items())))))}"

        return closure

    return _activity if fn is None else _activity(fn)
