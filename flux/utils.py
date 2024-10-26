from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Callable
from typing import Literal

import pandas as pd

from flux.errors import ExecutionTimeoutError


def call_with_timeout(
    func: Callable,
    type: Literal["Workflow", "Task"],
    name: str,
    id: str,
    timeout: int,
):
    if timeout > 0:
        with ThreadPoolExecutor(max_workers=1) as executor:
            try:
                future = executor.submit(func)
                return future.result(timeout)
            except TimeoutError:
                future.cancel()
                executor.shutdown(wait=False, cancel_futures=True)
                raise ExecutionTimeoutError(type, name, id, timeout)
    return func()


def make_hashable(item):
    if isinstance(item, dict):
        return tuple(sorted((k, make_hashable(v)) for k, v in item.items()))
    elif isinstance(item, list):
        return tuple(make_hashable(i) for i in item)
    elif isinstance(item, set):
        return frozenset(make_hashable(i) for i in item)
    elif isinstance(item, pd.DataFrame):
        return tuple(map(tuple, item.itertuples(index=False)))
    elif is_hashable(item):
        return item
    else:
        return str(item)


def is_hashable(obj) -> bool:
    try:
        hash(obj)
        return True
    except TypeError:
        return False
