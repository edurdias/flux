from datetime import datetime
from enum import Enum


class ExecutionEventType(str, Enum):

    WORKFLOW_STARTED = "WORKFLOW_STARTED"
    WORKFLOW_COMPLETED = "WORKFLOW_COMPLETED"
    WORKFLOW_FAILED = "WORKFLOW_FAILED"
    WORKFLOW_PAUSED = "WORKFLOW_PAUSED"
    WORKFLOW_RESUMED = "WORKFLOW_RESUMED"
    TASK_STARTED = "TASK_STARTED"
    TASK_RETRIED = "TASK_RETRIED"
    TASK_COMPLETED = "TASK_COMPLETED"
    TASK_FAILED = "TASK_FAILED"


class ExecutionEvent:

    def __init__(self, type: ExecutionEventType, id: str, name: str, value: any = None):
        self.type = type
        self.id = id
        self.name = name
        self.value = value
        self.time = datetime.now()
