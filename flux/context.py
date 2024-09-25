from flux.events import ExecutionEvent, ExecutionEventType


from typing import Generic, TypeVar
from uuid import uuid4

WorkflowInputType = TypeVar("InputType")


class WorkflowExecutionContext(Generic[WorkflowInputType]):

    _execution_id: str
    _name: str
    _input: WorkflowInputType
    _event_history: list[ExecutionEvent] = []

    def __init__(
        self, name: str, input: WorkflowInputType, execution_id: str = uuid4().hex
    ):
        self._name = name
        self._input = input
        self._execution_id = execution_id

    @property
    def name(self) -> str:
        return self._name

    @property
    def input(self) -> WorkflowInputType:
        return self._input

    @property
    def execution_id(self) -> str:
        return self._execution_id

    @property
    def event_history(self) -> list[ExecutionEvent]:
        return self._event_history

    def is_finished(self) -> bool:
        workflow_events = [
            e
            for e in self.event_history
            if e.type == ExecutionEventType.WORKFLOW_COMPLETED
            or e.type == ExecutionEventType.WORKFLOW_FAILED
        ]
        return len(workflow_events) > 0

    @property
    def output(self) -> any:
        completed = [
            e
            for e in self.event_history
            if e.type == ExecutionEventType.WORKFLOW_COMPLETED
        ]
        if len(completed) > 0:
            return completed[0].value
        return None
