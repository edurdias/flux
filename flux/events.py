from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from flux.utils import make_hashable


class ExecutionEventType(str, Enum):
    WORKFLOW_STARTED = "WORKFLOW_STARTED"
    WORKFLOW_PAUSED = "WORKFLOW_PAUSED"
    WORKFLOW_RESUMED = "WORKFLOW_RESUMED"
    WORKFLOW_COMPLETED = "WORKFLOW_COMPLETED"
    WORKFLOW_FAILED = "WORKFLOW_FAILED"

    TASK_STARTED = "TASK_STARTED"
    TASK_COMPLETED = "TASK_COMPLETED"
    TASK_FAILED = "TASK_FAILED"

    TASK_RETRY_STARTED = "TASK_RETRY_STARTED"
    TASK_RETRY_COMPLETED = "TASK_RETRY_COMPLETED"
    TASK_RETRY_FAILED = "TASK_RETRY_FAILED"

    TASK_FALLBACK_STARTED = "TASK_FALLBACK_STARTED"
    TASK_FALLBACK_COMPLETED = "TASK_FALLBACK_COMPLETED"
    TASK_FALLBACK_FAILED = "TASK_FALLBACK_FAILED"

    TASK_ROLLBACK_STARTED = "TASK_ROLLBACK_STARTED"
    TASK_ROLLBACK_COMPLETED = "TASK_ROLLBACK_COMPLETED"
    TASK_ROLLBACK_FAILED = "TASK_ROLLBACK_FAILED"


class ExecutionEvent:
    def __init__(
        self,
        type: ExecutionEventType,
        source_id: str,
        name: str,
        value: Any | None = None,
        time: datetime | None = None,
        id: str | None = None,
    ):
        self.type = type
        self.name = name
        self.source_id = source_id
        self.value = value
        self.time = time or datetime.now()
        self.id = id if id else self.__generate_id()

    def __eq__(self, other):
        if isinstance(other, ExecutionEvent):
            return self.id == other.id and self.type == other.type
        return False

    def __generate_id(self):
        args = {
            "name": self.name,
            "type": self.type,
            "source_id": self.source_id,
            "value": self.value,
        }
        return f"{abs(hash(tuple(sorted(make_hashable(args)))))}"
