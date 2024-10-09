import multiprocessing
from multiprocessing.managers import DictProxy
from typing import Any, Callable, Literal
from concurrent.futures import ThreadPoolExecutor, TimeoutError

from flux.exceptions import TimeoutException


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
                raise TimeoutException(type, name, id, timeout)
    return func()
