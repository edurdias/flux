from __future__ import annotations

import json
from typing import Any
from typing import Generic
from typing import TypeVar
from uuid import uuid4

from flux.events import ExecutionEvent
from flux.events import ExecutionEventType
from flux.utils import FluxEncoder

WorkflowInputType = TypeVar("WorkflowInputType")


class WorkflowExecutionContext(Generic[WorkflowInputType]):
    def __init__(
        self,
        name: str,
        input: WorkflowInputType,
        execution_id: str | None = None,
        events: list[ExecutionEvent] = [],
    ):
        self._execution_id = execution_id if execution_id else uuid4().hex
        self._name = name
        self._input = input
        self._events = events

    @property
    def execution_id(self) -> str:
        return self._execution_id

    @property
    def name(self) -> str:
        return self._name

    @property
    def input(self) -> WorkflowInputType:
        return self._input

    @property
    def events(self) -> list[ExecutionEvent]:
        return self._events

    @property
    def finished(self) -> bool:
        return len(self.events) > 0 and self.events[-1].type in (
            ExecutionEventType.WORKFLOW_COMPLETED,
            ExecutionEventType.WORKFLOW_FAILED,
        )

    @property
    def succeeded(self) -> bool:
        return self.finished and any(
            [e for e in self.events if e.type == ExecutionEventType.WORKFLOW_COMPLETED],
        )

    @property
    def failed(self) -> bool:
        return self.finished and any(
            [e for e in self.events if e.type == ExecutionEventType.WORKFLOW_FAILED],
        )

    @property
    def paused(self) -> bool:
        paused_events = [e for e in self.events if e.type == ExecutionEventType.WORKFLOW_PAUSED]
        resumed_events = [e for e in self.events if e.type == ExecutionEventType.WORKFLOW_RESUMED]
        return len(paused_events) > len(resumed_events)

    @property
    def output(self) -> Any:
        finished = [
            e
            for e in self.events
            if e.type
            in (
                ExecutionEventType.WORKFLOW_COMPLETED,
                ExecutionEventType.WORKFLOW_FAILED,
            )
        ]
        if len(finished) > 0:
            return finished[0].value
        return None

    def summary(self):
        return {key: value for key, value in self.to_dict().items() if key != "events"}

    def to_dict(self):
        return json.loads(self.to_json())

    def to_json(self):
        return json.dumps(self, indent=4, cls=FluxEncoder)
