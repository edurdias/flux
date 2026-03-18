from __future__ import annotations

import asyncio
import random
import uuid
from collections.abc import Coroutine
from datetime import datetime
from datetime import timedelta
from typing import Any
from typing import Callable

from flux.task import task


@task
async def now() -> datetime:
    return datetime.now()


@task
async def uuid4() -> uuid.UUID:
    return uuid.uuid4()


@task
async def choice(options: list[Any]) -> int:
    return random.choice(options)


@task
async def randint(a: int, b: int) -> int:
    return random.randint(a, b)


@task
async def randrange(start: int, stop: int | None = None, step: int = 1):
    return random.randrange(start, stop, step)


@task
async def parallel(*functions: Coroutine[Any, Any, Any]) -> list[Any]:
    tasks: list[asyncio.Task] = [asyncio.create_task(f) for f in functions]
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
