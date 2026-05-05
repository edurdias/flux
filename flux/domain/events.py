from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import Enum
from typing import Any, TypedDict

from flux.utils import FluxEncoder, make_hashable  # noqa: F401  (make_hashable kept for back-compat)


class PausedEventValue(TypedDict):
    name: str
    output: Any


class ExecutionState(str, Enum):
    CREATED = "CREATED"
    SCHEDULED = "SCHEDULED"
    CLAIMED = "CLAIMED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    PAUSED = "PAUSED"
    RESUMING = "RESUMING"
    RESUME_SCHEDULED = "RESUME_SCHEDULED"
    RESUME_CLAIMED = "RESUME_CLAIMED"
    CANCELLING = "CANCELLING"
    CANCELLED = "CANCELLED"


class ExecutionEventType(str, Enum):
    WORKFLOW_SCHEDULED = "WORKFLOW_SCHEDULED"
    WORKFLOW_CLAIMED = "WORKFLOW_CLAIMED"
    WORKFLOW_STARTED = "WORKFLOW_STARTED"
    WORKFLOW_COMPLETED = "WORKFLOW_COMPLETED"
    WORKFLOW_FAILED = "WORKFLOW_FAILED"
    WORKFLOW_PAUSED = "WORKFLOW_PAUSED"
    WORKFLOW_RESUMING = "WORKFLOW_RESUMING"
    WORKFLOW_RESUMED = "WORKFLOW_RESUMED"
    WORKFLOW_RESUME_SCHEDULED = "WORKFLOW_RESUME_SCHEDULED"
    WORKFLOW_RESUME_CLAIMED = "WORKFLOW_RESUME_CLAIMED"
    WORKFLOW_CANCELLING = "WORKFLOW_CANCELLING"
    WORKFLOW_CANCELLED = "WORKFLOW_CANCELLED"

    TASK_STARTED = "TASK_STARTED"
    TASK_COMPLETED = "TASK_COMPLETED"
    TASK_FAILED = "TASK_FAILED"
    TASK_PAUSED = "TASK_PAUSED"
    TASK_RESUMED = "TASK_RESUMED"
    TASK_PROGRESS = "TASK_PROGRESS"

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
        subject: str | None = None,
    ):
        self.type = type
        self.name = name
        self.source_id = source_id
        self.value = value
        self.subject = subject

        self.time = time or datetime.now()

        self.id = id if id else self.__generate_id()

    def __eq__(self, other):
        if isinstance(other, ExecutionEvent):
            return self.id == other.id and self.type == other.type
        return False

    def __generate_id(self):
        # SHA256 of canonical JSON. Python's built-in hash() is randomized
        # per-process under ASLR, so event IDs computed in one process did
        # not match the same event re-derived in another, breaking the
        # `(event_id, type)` dedup key in ContextManager._get_additional_events
        # across server restarts and worker handoff.
        args = {
            "name": self.name,
            "type": self.type,
            "source_id": self.source_id,
            "value": self.value,
            "time": self.time,
        }
        # FluxEncoder is its own fallback (its default() ends in `return str(obj)`),
        # so passing `default=...` here would override FluxEncoder.default and the
        # canonical Enum/datetime handling we want.
        canonical = json.dumps(args, sort_keys=True, cls=FluxEncoder)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
