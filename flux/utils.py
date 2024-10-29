from __future__ import annotations

import inspect
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from datetime import timedelta
from enum import Enum
from types import GeneratorType
from typing import Callable
from typing import Literal

import flux.context as context
from flux.errors import ExecutionError
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
    elif type(item).__name__ == "pandas.DataFrame":
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


def to_json(obj):
    return json.dumps(obj, indent=4, cls=FluxEncoder)


class FluxEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Enum):
            return obj.value
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, context.WorkflowExecutionContext):
            return {
                "name": obj.name,
                "execution_id": obj.execution_id,
                "input": obj.input,
                "output": obj.output,
                "events": obj.events,
            }

        if isinstance(obj, ExecutionError):
            obj = obj.inner_exception if obj.inner_exception else obj
            return {"type": type(obj).__name__, "message": str(obj)}

        if isinstance(obj, Exception):
            return {"type": type(obj).__name__, "message": str(obj)}

        if inspect.isclass(type(obj)) and isinstance(obj, Callable):
            return type(obj).__name__

        if isinstance(obj, Callable):
            return obj.__name__

        if isinstance(obj, GeneratorType):
            return str(obj)

        if isinstance(obj, timedelta):
            return obj.total_seconds()

        if isinstance(obj, uuid.UUID):
            return str(obj)

        if hasattr(obj, "__dict__"):
            return obj.__dict__

        return str(obj)
