import json
from datetime import datetime
from enum import Enum


class EventHistoryEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Enum):
            return obj.value
        if isinstance(obj, datetime):
            return obj.isoformat()
        return obj.__dict__