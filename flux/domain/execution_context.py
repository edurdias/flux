from __future__ import annotations

import json
from collections.abc import Awaitable
from contextvars import ContextVar
from contextvars import Token
from typing import Any
from typing import Callable
from typing import Generic
from typing import Self
from typing import TypeVar
from uuid import uuid4

from flux.domain.events import ExecutionEvent
from flux.domain.events import ExecutionEventType
from flux.domain.events import ExecutionState
from flux.errors import ExecutionError
from flux.utils import FluxEncoder
from flux.utils import maybe_awaitable
from flux.worker_registry import WorkerInfo
from flux.domain import ResourceRequest

WorkflowInputType = TypeVar("WorkflowInputType")
CURRENT_CONTEXT: ContextVar = ContextVar("current_context", default=None)


class ExecutionContext(Generic[WorkflowInputType]):
    def __init__(
        self,
        workflow_id: str,
        workflow_name: str,
        input: WorkflowInputType | None = None,
        execution_id: str | None = None,
        state: ExecutionState | None = None,
        events: list[ExecutionEvent] | None = None,
        checkpoint: Callable[[ExecutionContext], Awaitable] | None = None,
        requests: ResourceRequest | None = None,
        current_worker: str | None = None,
    ):
        self._workflow_id = workflow_id
        self._workflow_name = workflow_name
        self._input = input
        self._execution_id = execution_id or uuid4().hex
        self._events = events or []
        self._state = state or ExecutionState.CREATED
        self._checkpoint = checkpoint or (lambda _: maybe_awaitable(None))
        self._requests = requests or None
        self._current_worker = current_worker or ""

    @staticmethod
    async def get() -> ExecutionContext:
        ctx = CURRENT_CONTEXT.get()
        if ctx is None:
            raise ExecutionError(
                message="No active WorkflowExecutionContext found. Make sure you are running inside a workflow or task execution.",
            )
        return ctx

    @staticmethod
    def set(ctx: ExecutionContext) -> Token:
        return CURRENT_CONTEXT.set(ctx)

    @staticmethod
    def reset(token: Token) -> None:
        CURRENT_CONTEXT.reset(token)

    @property
    def execution_id(self) -> str:
        return self._execution_id

    @property
    def workflow_id(self) -> str:
        return self._workflow_id

    @property
    def workflow_name(self) -> str:
        return self._workflow_name

    @property
    def current_worker(self) -> str:
        return self._current_worker

    @property
    def input(self) -> WorkflowInputType:
        return self._input  # type: ignore [return-value]

    @property
    def events(self) -> list[ExecutionEvent]:
        return self._events

    @property
    def state(self) -> ExecutionState:
        return self._state

    @property
    def has_finished(self) -> bool:
        return len(self.events) > 0 and self.events[-1].type in (
            ExecutionEventType.WORKFLOW_COMPLETED,
            ExecutionEventType.WORKFLOW_FAILED,
            ExecutionEventType.WORKFLOW_CANCELLED,
        )

    @property
    def has_succeeded(self) -> bool:
        return self.has_finished and any(
            [e for e in self.events if e.type == ExecutionEventType.WORKFLOW_COMPLETED],
        )

    @property
    def has_failed(self) -> bool:
        return self.has_finished and any(
            [e for e in self.events if e.type == ExecutionEventType.WORKFLOW_FAILED],
        )

    @property
    def is_paused(self) -> bool:
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
    def is_cancelled(self) -> bool:
        """
        Check if the execution is currently cancelled.

        Returns:
            bool: True if the last event is a workflow cancelled event, False otherwise.
        """
        if self.events:
            last_event = self.events[-1]
            if last_event.type == ExecutionEventType.WORKFLOW_CANCELLED:
                return True
        return False

    @property
    def is_cancelling(self) -> bool:
        """
        Check if the execution is currently in the process of being cancelled.

        Returns:
            bool: True if the last event is a workflow cancelling event, False otherwise.
        """
        if self.events:
            last_event = self.events[-1]
            if last_event.type == ExecutionEventType.WORKFLOW_CANCELLING:
                return True
        return False

    @property
    def is_claimed(self) -> bool:
        """
        Check if the execution is currently claimed by a worker.

        Returns:
            bool: True if the last event is a workflow claimed event, False otherwise.
        """
        if self.events:
            last_event = self.events[-1]
            if last_event.type == ExecutionEventType.WORKFLOW_CLAIMED:
                return True
        return False

    @property
    def has_resumed(self) -> bool:
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
    def has_started(self) -> bool:
        return any(e.type == ExecutionEventType.WORKFLOW_STARTED for e in self.events)

    @property
    def is_scheduled(self) -> bool:
        return self.state == ExecutionState.SCHEDULED and any(
            e.type == ExecutionEventType.WORKFLOW_SCHEDULED for e in self.events
        )

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

    def schedule(self, worker: WorkerInfo) -> Self:
        self._state = ExecutionState.SCHEDULED
        self.events.append(
            ExecutionEvent(
                type=ExecutionEventType.WORKFLOW_SCHEDULED,
                source_id=worker.name,
                name=worker.name,
            ),
        )
        return self

    def claim(self, worker: WorkerInfo) -> Self:
        self._current_worker = worker.name
        self._state = ExecutionState.CLAIMED
        self.events.append(
            ExecutionEvent(
                type=ExecutionEventType.WORKFLOW_CLAIMED,
                source_id=worker.name,
                name=worker.name,
            ),
        )
        return self

    def start(self, id: str) -> Self:
        self._state = ExecutionState.RUNNING
        self.events.append(
            ExecutionEvent(
                type=ExecutionEventType.WORKFLOW_STARTED,
                source_id=id,
                name=self.workflow_name,
                value=self.input,
            ),
        )
        return self

    def resume(self, id: str) -> Self:
        self._state = ExecutionState.RUNNING
        self.events.append(
            ExecutionEvent(
                type=ExecutionEventType.WORKFLOW_RESUMED,
                source_id=id,
                name=self.workflow_name,
                value=self.input,
            ),
        )
        return self

    def pause(self, id: str, name: str) -> Self:
        self._state = ExecutionState.PAUSED
        self.events.append(
            ExecutionEvent(
                type=ExecutionEventType.WORKFLOW_PAUSED,
                source_id=id,
                name=self.workflow_name,
                value=name,
            ),
        )
        return self

    def complete(self, id: str, output: Any) -> Self:
        self._state = ExecutionState.COMPLETED
        self.events.append(
            ExecutionEvent(
                type=ExecutionEventType.WORKFLOW_COMPLETED,
                source_id=id,
                name=self.workflow_name,
                value=output,
            ),
        )
        return self

    def fail(self, id: str, output: Any) -> Self:
        self._state = ExecutionState.FAILED
        self.events.append(
            ExecutionEvent(
                type=ExecutionEventType.WORKFLOW_FAILED,
                source_id=id,
                name=self.workflow_name,
                value=output,
            ),
        )
        return self

    def start_cancel(self) -> Self:
        self._state = ExecutionState.CANCELLING
        self.events.append(
            ExecutionEvent(
                type=ExecutionEventType.WORKFLOW_CANCELLING,
                source_id=self._current_worker,
                name=self.workflow_name,
            ),
        )
        return self

    def cancel(self) -> Self:
        if not self.is_cancelling:
            self.start_cancel()

        self._state = ExecutionState.CANCELLED
        self.events.append(
            ExecutionEvent(
                type=ExecutionEventType.WORKFLOW_CANCELLED,
                source_id=self._current_worker,
                name=self.workflow_name,
            ),
        )
        return self

    async def checkpoint(self) -> Awaitable:
        return await maybe_awaitable(self._checkpoint(self))

    def set_checkpoint(self, checkpoint: Callable[[ExecutionContext], Awaitable]) -> Self:
        self._checkpoint = checkpoint
        return self

    def summary(self):
        return {key: value for key, value in self.to_dict().items() if key != "events"}

    def to_dict(self):
        return json.loads(self.to_json())

    def to_json(self):
        return json.dumps(self, indent=4, cls=FluxEncoder)

    @staticmethod
    def from_json(
        data: dict,
        checkpoint: Callable[[ExecutionContext], Awaitable] | None = None,
    ) -> ExecutionContext:
        return ExecutionContext(
            workflow_id=data["workflow_id"],
            workflow_name=data["workflow_name"],
            input=data["input"],
            execution_id=data["execution_id"],
            state=data["state"],
            events=[ExecutionEvent(**event) for event in data["events"]],
            checkpoint=checkpoint,
        )
