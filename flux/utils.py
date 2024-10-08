from typing import Callable
from concurrent.futures import ThreadPoolExecutor


def call_with_timeout(
    func: Callable, timeout: int, timeout_message: str, timeout_handler: Callable = None
):
    if timeout > 0:
        with ThreadPoolExecutor(max_workers=1) as executor:
            try:
                future = executor.submit(func)
                return future.result(timeout)
            except TimeoutError:
                error = TimeoutError(timeout_message)
                if timeout_handler:
                    timeout_handler(error)
                else:
                    raise error
    return func()
