import json
from datetime import datetime
from enum import Enum


class WorkflowContextEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Enum):
            return obj.value
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, list):
            return obj
        return obj.__dict__
