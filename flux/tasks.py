import os
import time
import uuid
import random

from typing import Callable, NoReturn
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

import flux.decorators as d
from flux.executors import WorkflowExecutor
from flux.exceptions import WorkflowPausedException


@d.task
def now() -> datetime:
    return datetime.now()


@d.task
def uuid4() -> uuid.UUID:
    return uuid.uuid4()


@d.task
def randint(a: int, b: int) -> int:
    return random.randint(a, b)


@d.task
def randrange(start: int, stop: int | None = None, step: int = 1):
    return random.randrange(start, stop, step)


@d.task
def parallel(*functions: Callable):
    results = []
    with ThreadPoolExecutor(max_workers=os.cpu_count()) as executor:
        futures = [executor.submit(func) for func in functions]
        for future in as_completed(futures):
            result = yield future.result()
            results.append(result)
    return results


@d.task
def sleep(duration: float | timedelta) -> NoReturn:
    """
    Pauses the execution of the workflow for a given duration.

    :param duration: The amount of time to sleep.
        - If `duration` is a float, it represents the number of seconds to sleep.
        - If `duration` is a timedelta, it will be converted to seconds using the `total_seconds()` method.

    :raises TypeError: If `duration` is neither a float nor a timedelta.
    """
    if isinstance(duration, timedelta):
        duration = duration.total_seconds()
    time.sleep(duration)


@d.task.with_options(name="pause_$reference")
def pause(reference: str) -> NoReturn:
    raise WorkflowPausedException(reference)


@d.task.with_options(name="call_workflow_$workflow")
def call_workflow(workflow: str | d.workflow, input: any = None):
    name = workflow.name if isinstance(workflow, d.workflow) else str(workflow)
    return WorkflowExecutor.current().execute(name, input).output


@d.task
def pipeline(tasks: list[d.task], input: any):
    result = input
    for task in tasks:
        result = yield task(result)
    return result
