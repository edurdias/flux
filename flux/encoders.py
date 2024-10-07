import json
from enum import Enum
from datetime import datetime
import flux.context as context


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
        if isinstance(obj, Exception):
            return {"type": type(obj).__name__, "message": str(obj)}
        return obj.__dict__
