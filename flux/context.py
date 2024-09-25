from flux.events import ExecutionEvent, ExecutionEventType


from typing import Generic, List, TypeVar
from uuid import uuid4

WorkflowInputType = TypeVar("InputType")

class WorkflowExecutionContext(Generic[WorkflowInputType]):

    _execution_id: str
    _name: str
    _input: WorkflowInputType
    _eventHistory: List[ExecutionEvent] = []

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
    def eventHistory(self) -> List[ExecutionEvent]:
        return self._eventHistory

    def is_finished(self) -> bool:
        workflow_events = [
            e
            for e in self.eventHistory
            if e.type == ExecutionEventType.WORKFLOW_COMPLETED
            or e.type == ExecutionEventType.WORKFLOW_FAILED
        ]
        return len(workflow_events) > 0