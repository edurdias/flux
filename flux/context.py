from __future__ import annotations

import json
from contextvars import ContextVar
from contextvars import Token
from typing import Any
from typing import Generic
from typing import TypeVar
from uuid import uuid4

from flux.errors import ExecutionError
from flux.events import ExecutionEvent
from flux.events import ExecutionEventType
from flux.utils import FluxEncoder

WorkflowInputType = TypeVar("WorkflowInputType")
CURRENT_CONTEXT: ContextVar = ContextVar("current_context", default=None)


class WorkflowExecutionContext(Generic[WorkflowInputType]):
    def __init__(
        self,
        name: str,
        input: WorkflowInputType | None = None,
        execution_id: str | None = None,
        events: list[ExecutionEvent] | None = None,
    ):
        self._name = name
        self._input = input
        self._execution_id = execution_id if execution_id else uuid4().hex
        self._events = list(events) if events else []

    @staticmethod
    async def get() -> WorkflowExecutionContext:
        ctx = CURRENT_CONTEXT.get()
        if ctx is None:
            raise ExecutionError(
                message="No active WorkflowExecutionContext found. Make sure you are running inside a workflow or task execution.",
            )
        return ctx

    @staticmethod
    def set(ctx: WorkflowExecutionContext) -> Token:
        return CURRENT_CONTEXT.set(ctx)

    @staticmethod
    def reset(token: Token) -> None:
        CURRENT_CONTEXT.reset(token)

    @property
    def execution_id(self) -> str:
        return self._execution_id

    @property
    def name(self) -> str:
        return self._name

    @property
    def input(self) -> WorkflowInputType:
        return self._input  # type: ignore [return-value]

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
        """
        Check if the execution is currently paused.

        Returns:
            bool: True if the last execution event is a WORKFLOW_PAUSED event, False otherwise.
        """
        if self.events:
            last_event = self.events[-1]
            if last_event.type == ExecutionEventType.WORKFLOW_PAUSED:
                return True
        return False

    @property
    def resumed(self) -> bool:
        """
        Checks if the workflow is currently in a resumed state.

        Returns:
            bool: True if the last event is a workflow resume event, False otherwise.
        """
        if self.events:
            last_event = self.events[-1]
            if last_event.type == ExecutionEventType.WORKFLOW_RESUMED:
                return True
        return False

    @property
    def started(self) -> bool:
        return any(e.type == ExecutionEventType.WORKFLOW_STARTED for e in self.events)

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
