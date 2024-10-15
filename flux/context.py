import json
from uuid import uuid4
from typing import Generic, TypeVar

from flux.encoders import WorkflowContextEncoder
from flux.events import ExecutionEvent, ExecutionEventType

WorkflowInputType = TypeVar("InputType")


class WorkflowExecutionContext(Generic[WorkflowInputType]):

    def __init__(
        self,
        name: str,
        input: WorkflowInputType,
        execution_id: str = None,
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
        workflow_events = [
            e
            for e in self.events
            if e.type == ExecutionEventType.WORKFLOW_COMPLETED
            or e.type == ExecutionEventType.WORKFLOW_FAILED
        ]
        return len(workflow_events) > 0

    @property
    def output(self) -> any:
        completed = [
            e for e in self.events if e.type == ExecutionEventType.WORKFLOW_COMPLETED
        ]
        if len(completed) > 0:
            return completed[0].value
        return None

    def summary(self):
        return {
            "execution_id": self.execution_id,
            "name": self.name,
            "input": self.input,
            "output": self.output,
        }

    def to_dict(self):
        return json.loads(self.to_json())

    def to_json(self):
        return json.dumps(self, indent=4, cls=WorkflowContextEncoder)
