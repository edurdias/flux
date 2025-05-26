from __future__ import annotations

import asyncio
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
from flux.errors import ExecutionError, CancelationRequested
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
    ):
        self._workflow_id = workflow_id
        self._workflow_name = workflow_name
        self._input = input
        self._execution_id = execution_id or uuid4().hex
        self._events = events or []
        self._state = state or ExecutionState.CREATED
        self._checkpoint = checkpoint or (lambda _: maybe_awaitable(None))
        self._requests = requests or None
        self._cancel_event = asyncio.Event()

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
            ExecutionEventType.WORKFLOW_CANCELED,
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
    def has_canceled(self) -> bool:
        """
        Check if the execution has been canceled.

        Returns:
            bool: True if the execution was canceled, False otherwise.
        """
        return self.has_finished and any(
            [e for e in self.events if e.type == ExecutionEventType.WORKFLOW_CANCELED],
        )

    @property
    def has_started(self) -> bool:
        return any(e.type == ExecutionEventType.WORKFLOW_STARTED for e in self.events)

    @property
    def has_resumed(self) -> bool:
        """
        Check if the execution has been resumed.

        Returns:
            bool: True if there's a WORKFLOW_RESUMED event in the events list, False otherwise.
        """
        return any(e.type == ExecutionEventType.WORKFLOW_RESUMED for e in self.events)

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

    def cancel(self, id: str, reason: str = "Operation canceled") -> Self:
        """
        Mark the execution as canceled.

        Args:
            id (str): The ID of the entity that triggered the cancellation.
            reason (str, optional): The reason for cancellation. Defaults to "Operation canceled".

        Returns:
            Self: The execution context instance.
        """
        self._state = ExecutionState.CANCELED
        self.events.append(
            ExecutionEvent(
                type=ExecutionEventType.WORKFLOW_CANCELED,
                source_id=id,
                name=self.workflow_name,
                value=reason,
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
    def from_json(data: dict) -> ExecutionContext:
        return ExecutionContext(
            workflow_id=data["workflow_id"],
            workflow_name=data["workflow_name"],
            input=data["input"],
            execution_id=data["execution_id"],
            state=data["state"],
            events=[ExecutionEvent(**event) for event in data["events"]],
        )

    @property
    def _requests(self) -> ResourceRequest | None:
        return self.__requests

    @_requests.setter
    def _requests(self, value: ResourceRequest | None):
        self.__requests = value

    @property
    def cancel_event(self) -> asyncio.Event:
        """
        Get the cancellation event for this execution context.

        Returns:
            asyncio.Event: The cancellation event.
        """
        return self._cancel_event

    async def check_cancellation(self) -> None:
        """
        Check if this execution has been canceled.

        Raises:
            CancelationRequested: If the execution has been canceled.
        """
        if self._cancel_event.is_set():
            raise CancelationRequested()

    def set_cancellation(self) -> None:
        """
        Signal that this execution should be canceled.
        """
        self._cancel_event.set()
