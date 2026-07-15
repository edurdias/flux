from __future__ import annotations

import asyncio
import random
import uuid
from collections.abc import Coroutine
from datetime import datetime
from datetime import timedelta
from typing import Any
from collections.abc import Callable

from flux.errors import PauseRequested
from flux.task import task


@task
async def now() -> datetime:
    return datetime.now()


@task
async def uuid4() -> uuid.UUID:
    return uuid.uuid4()


@task
async def choice(options: list[Any]) -> Any:
    return random.choice(options)


@task
async def randint(a: int, b: int) -> int:
    return random.randint(a, b)


@task
async def randrange(start: int, stop: int | None = None, step: int = 1):
    return random.randrange(start, stop, step)


@task
async def parallel(
    *functions: Coroutine[Any, Any, Any],
    max_concurrent: int | None = None,
    raise_on_error: bool = True,
) -> list[Any]:
    """Run coroutines concurrently and return their results in input order.

    :param max_concurrent: Maximum number of coroutines running at once.
        None (default) runs everything concurrently.
    :param raise_on_error: If True (default), the first exception propagates
        and fails the whole batch. If False, a failed coroutine's slot in the
        result list becomes None and the remaining coroutines keep running;
        the failure is still recorded on the corresponding task's events.
    """
    if max_concurrent is not None and max_concurrent < 1:
        for function in functions:
            function.close()
        raise ValueError(f"max_concurrent must be >= 1, got {max_concurrent}")

    semaphore = asyncio.Semaphore(max_concurrent) if max_concurrent else None

    async def bounded(function: Coroutine[Any, Any, Any]) -> Any:
        if semaphore is None:
            return await function
        async with semaphore:
            return await function

    async def dropping(function: Coroutine[Any, Any, Any]) -> Any:
        # Pause and cancellation are control flow, not item failures — they
        # must keep propagating (immediately, through the plain gather below)
        # or a paused branch would silently become None. CancelledError and
        # other BaseExceptions are not caught by `except Exception`.
        try:
            return await bounded(function)
        except PauseRequested:
            raise
        except Exception:
            return None

    runner = bounded if raise_on_error else dropping
    tasks: list[asyncio.Task] = [asyncio.create_task(runner(f)) for f in functions]
    return await asyncio.gather(*tasks)


@task
async def sleep(duration: float | timedelta):
    """
    Pauses the execution of the workflow for a given duration.

    :param duration: The amount of time to sleep.
        - If `duration` is a float, it represents the number of seconds to sleep.
        - If `duration` is a timedelta, it will be converted to seconds using the `total_seconds()` method.

    :raises TypeError: If `duration` is neither a float nor a timedelta.
    """
    if isinstance(duration, timedelta):
        duration = duration.total_seconds()
    await asyncio.sleep(duration)


@task
async def pipeline(*tasks: Callable, input: Any):
    result = input
    for t in tasks:
        result = await t(result)
    return result
