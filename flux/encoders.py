import json
from enum import Enum
from datetime import datetime, timedelta
from types import GeneratorType
from typing import Callable
import uuid
import flux.context as context
from flux.exceptions import ExecutionException


class WorkflowContextEncoder(json.JSONEncoder):
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

        if isinstance(obj, ExecutionException):
            obj = obj.inner_exception if obj.inner_exception else obj
            return {"type": type(obj).__name__, "message": str(obj)}

        if isinstance(obj, Exception):
            return {"type": type(obj).__name__, "message": str(obj)}

        if isinstance(obj, Callable):
            return obj.__name__

        if isinstance(obj, GeneratorType):
            return str(obj)

        if isinstance(obj, timedelta):
            return obj.total_seconds()

        if isinstance(obj, uuid.UUID):
            return str(obj)

        return obj.__dict__
