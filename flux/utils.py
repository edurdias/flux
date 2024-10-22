import multiprocessing
from multiprocessing.managers import DictProxy
from typing import Any, Callable, Literal
from concurrent.futures import ThreadPoolExecutor, TimeoutError

from flux.errors import TimeoutError


def call_with_timeout(
    func: Callable, type: Literal["Workflow", "Task"], name: str, id: str, timeout: int
):
    if timeout > 0:
        with ThreadPoolExecutor(max_workers=1) as executor:
            try:
                future = executor.submit(func)
                return future.result(timeout)
            except TimeoutError:
                future.cancel()
                executor.shutdown(wait=False, cancel_futures=True)
                raise TimeoutError(type, name, id, timeout)
    return func()


def make_hashable(item):
    if isinstance(item, dict):
        return tuple(sorted((k, make_hashable(v)) for k, v in item.items()))
    elif isinstance(item, list):
        return tuple(make_hashable(i) for i in item)
    elif isinstance(item, set):
        return frozenset(make_hashable(i) for i in item)
    else:
        return item
