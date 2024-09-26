from datetime import datetime
from enum import Enum


class ExecutionEventType(str, Enum):

    WORKFLOW_STARTED = "WorkflowStarted"
    WORKFLOW_COMPLETED = "WorkflowCompleted"
    WORKFLOW_FAILED = "WorkflowFailed"
    WORKFLOW_PAUSED = "WorkflowPaused"
    WORKFLOW_RESUMED = "WorkflowResumed"

    ACTIVITY_STARTED = "ActivityStarted"
    ACTIVITY_COMPLETED = "ActivityCompleted"
    ACTIVITY_FAILED = "ActivityFailed"


class ExecutionEvent:
    type: ExecutionEventType
    id: str
    name: str
    value: any
    time: datetime

    def __init__(self, type: ExecutionEventType, id: str, name: str, value: any = None):
        self.type = type
        self.id = id
        self.name = name
        self.value = value
        self.time = datetime.now()
