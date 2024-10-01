from datetime import datetime
from enum import Enum


class ExecutionEventType(str, Enum):

    WORKFLOW_STARTED = "WORKFLOW_STARTED"
    WORKFLOW_COMPLETED = "WORKFLOW_COMPLETED"
    WORKFLOW_FAILED = "WORKFLOW_FAILED"
    WORKFLOW_PAUSED = "WORKFLOW_PAUSED"
    WORKFLOW_RESUMED = "WORKFLOW_RESUMED"
    ACTIVITY_STARTED = "ACTIVITY_STARTED"
    ACTIVITY_RETRIED = "ACTIVITY_RETRIED"
    ACTIVITY_COMPLETED = "ACTIVITY_COMPLETED"
    ACTIVITY_FAILED = "ACTIVITY_FAILED"


class ExecutionEvent:
    type: ExecutionEventType
    id: str
    name: str
    value: any
    time: datetime

    def __init__(self, type: ExecutionEventType, id: str, name: str, value: any = None, **kwargs):
        self.type = type
        self.id = id
        self.name = name
        self.value = value
        self.time = datetime.now()
