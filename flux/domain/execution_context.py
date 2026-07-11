from __future__ import annotations

import json
from collections.abc import Awaitable
from contextvars import ContextVar
from contextvars import Token
from typing import Any
from collections.abc import Callable
from typing import Generic
from typing import Self
from typing import TypeVar
from uuid import uuid4

from flux.domain.events import ExecutionEvent
from flux.domain.events import ExecutionEventType
from flux.domain.events import ExecutionState
from flux.domain.events import PausedEventValue
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
        workflow_namespace: str,
        workflow_name: str,
        input: WorkflowInputType | None = None,
        execution_id: str | None = None,
        state: ExecutionState | None = None,
        events: list[ExecutionEvent] | None = None,
        checkpoint: Callable[[ExecutionContext], Awaitable] | None = None,
        requests: ResourceRequest | None = None,
        current_worker: str | None = None,
        progress_callback: Callable | None = None,
    ):
        self._workflow_id = workflow_id
        self._workflow_namespace = workflow_namespace
        self._workflow_name = workflow_name
        self._input = input
        self._execution_id = execution_id or uuid4().hex
        self._events = events or []
        # Wire rebuilds (from_json / claim responses) carry the state as its
        # string value; coerce so state-derived flags (has_finished,
        # is_paused, …) hold on rebuilt contexts, not only after the first
        # local transition.
        if isinstance(state, str):
            state = ExecutionState(state.upper())
        self._state = state or ExecutionState.CREATED
        self._checkpoint = checkpoint or (lambda _: maybe_awaitable(None))
        self._requests = requests or None
        self._current_worker = current_worker or ""
        self._progress_callback = progress_callback or (lambda *_: None)
        self._exec_token: str | None = None
        # Transient executions never persist: checkpoint stays the no-op and
        # durable-only features (pause, approvals) must refuse to engage.
        # In-memory only — deliberately absent from to_dict()/pickle.
        self._transient = False
        # Per-run occurrence counter for repeated identical task calls.
        # Runtime-only (never serialized): workflow code is deterministic, so
        # each run — original or replay — consumes occurrences in the same
        # order and derives the same per-call ids.
        self._task_occurrences: dict[str, int] = {}

    def mark_transient(self) -> ExecutionContext:
        self._transient = True
        return self

    @property
    def is_transient(self) -> bool:
        return getattr(self, "_transient", False)

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
    def workflow_namespace(self) -> str:
        return self._workflow_namespace

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

    def next_task_occurrence(self, task_id: str) -> int:
        """Return how many times ``task_id`` was already called this run.

        The task engine uses this to give repeated identical calls distinct
        per-call ids: without it, the replay short-circuit collapses the
        second ``await send_email(x)`` into the first call's stored output.
        """
        occurrence = self._task_occurrences.get(task_id, 0)
        self._task_occurrences[task_id] = occurrence + 1
        return occurrence

    @property
    def has_finished(self) -> bool:
        last = self._last_workflow_event()
        return last is not None and last.type in (
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
        return self._is_last_event(ExecutionEventType.WORKFLOW_PAUSED)

    @property
    def is_resuming(self) -> bool:
        """
        Check if the execution is in the RESUME_CLAIMED state — i.e. the
        worker has claimed the resume and is about to invoke `ctx.resume()`
        inside the paused task's body.
        """
        return self._state == ExecutionState.RESUME_CLAIMED

    @property
    def is_cancelled(self) -> bool:
        """
        Check if the execution is currently cancelled.

        Returns:
            bool: True if the last event is a workflow cancelled event, False otherwise.
        """
        return self._is_last_event(ExecutionEventType.WORKFLOW_CANCELLED)

    @property
    def is_cancelling(self) -> bool:
        """
        Check if the execution is currently in the process of being cancelled.

        Returns:
            bool: True if the last event is a workflow cancelling event, False otherwise.
        """
        return self._is_last_event(ExecutionEventType.WORKFLOW_CANCELLING)

    @property
    def is_claimed(self) -> bool:
        """
        Check if the execution is currently claimed by a worker.

        Returns:
            bool: True if the last event is a workflow claimed event, False otherwise.
        """
        return self._is_last_event(ExecutionEventType.WORKFLOW_CLAIMED)

    @property
    def has_resumed(self) -> bool:
        """
        Checks if the workflow is currently in a resumed state.

        Returns:
            bool: True if the last event is a workflow resume event, False otherwise.
        """
        return self._is_last_event(ExecutionEventType.WORKFLOW_RESUMED)

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
        # Pin the row to the scheduling worker so the claim path can lock and
        # filter on worker_name — symmetric with resume_schedule below.
        self._current_worker = worker.name
        self._state = ExecutionState.SCHEDULED
        self.events.append(
            ExecutionEvent(
                type=ExecutionEventType.WORKFLOW_SCHEDULED,
                source_id=worker.name,
                name=worker.name,
                subject=None,
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
                subject=None,
            ),
        )
        return self

    def resume_schedule(self, worker: WorkerInfo) -> Self:
        if self._state != ExecutionState.RESUMING:
            raise ExecutionError(
                message=(
                    f"Cannot schedule resume: state is {self._state.value}, "
                    f"expected {ExecutionState.RESUMING.value}"
                ),
            )
        self._current_worker = worker.name
        self._state = ExecutionState.RESUME_SCHEDULED
        self.events.append(
            ExecutionEvent(
                type=ExecutionEventType.WORKFLOW_RESUME_SCHEDULED,
                source_id=worker.name,
                name=worker.name,
                subject=None,
            ),
        )
        return self

    def resume_claim(self, worker: WorkerInfo) -> Self:
        if self._state != ExecutionState.RESUME_SCHEDULED:
            raise ExecutionError(
                message=(
                    f"Cannot claim resume: state is {self._state.value}, "
                    f"expected {ExecutionState.RESUME_SCHEDULED.value}"
                ),
            )
        if self._current_worker is not None and self._current_worker != worker.name:
            raise ExecutionError(
                message=(
                    f"Cannot claim resume: scheduled for worker "
                    f"'{self._current_worker}', not '{worker.name}'"
                ),
            )
        self._current_worker = worker.name
        self._state = ExecutionState.RESUME_CLAIMED
        self.events.append(
            ExecutionEvent(
                type=ExecutionEventType.WORKFLOW_RESUME_CLAIMED,
                source_id=worker.name,
                name=worker.name,
                subject=None,
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
                subject=None,
            ),
        )
        return self

    def start_resuming(self, input: Any | None = None) -> Self:
        if self._state != ExecutionState.PAUSED:
            raise ExecutionError(
                message=(
                    f"Cannot start resuming: state is {self._state.value}, "
                    f"expected {ExecutionState.PAUSED.value}"
                ),
            )
        return self._record_resuming(input)

    def force_start_resuming(self, input: Any | None = None) -> Self:
        """Transition to RESUMING regardless of the current state.

        An approval can be decided before the worker has recorded the
        WORKFLOW_PAUSED transition, so the execution may still be RUNNING or
        CLAIMED when the decide handler needs to queue the resume. Only the
        approval decide path should use this; normal resumes go through
        start_resuming(), which enforces the PAUSED precondition.
        """
        return self._record_resuming(input)

    def _record_resuming(self, input: Any | None = None) -> Self:
        self._state = ExecutionState.RESUMING
        self.events.append(
            ExecutionEvent(
                type=ExecutionEventType.WORKFLOW_RESUMING,
                source_id=self._current_worker,
                name=self.workflow_name,
                value=input,
                subject=None,
            ),
        )
        return self

    def resume(self) -> Any:
        if self._state != ExecutionState.RESUME_CLAIMED:
            raise ExecutionError(
                message=(
                    f"Cannot resume: state is {self._state.value}, "
                    f"expected {ExecutionState.RESUME_CLAIMED.value}"
                ),
            )

        resuming_events = [e for e in self.events if e.type == ExecutionEventType.WORKFLOW_RESUMING]
        event = next(reversed(resuming_events), None)

        if not event:
            raise ExecutionError(
                message="Cannot resume workflow: no resuming event found.",
            )

        self._state = ExecutionState.RUNNING
        self.events.append(
            ExecutionEvent(
                type=ExecutionEventType.WORKFLOW_RESUMED,
                source_id=self._current_worker,
                name=self.workflow_name,
                value=event.value,
                subject=None,
            ),
        )
        return event.value

    def pause(self, id: str, name: str, output: Any = None) -> Self:
        self._state = ExecutionState.PAUSED
        self.events.append(
            ExecutionEvent(
                type=ExecutionEventType.WORKFLOW_PAUSED,
                source_id=id,
                name=self.workflow_name,
                value=PausedEventValue(name=name, output=output),
                subject=None,
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
                subject=None,
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
                subject=None,
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
                subject=None,
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
                subject=None,
            ),
        )
        return self

    async def checkpoint(self) -> Awaitable:
        return await maybe_awaitable(self._checkpoint(self))

    def set_checkpoint(self, checkpoint: Callable[[ExecutionContext], Awaitable]) -> Self:
        self._checkpoint = checkpoint
        return self

    async def emit_progress(self, task_id: str, task_name: str, value: Any) -> None:
        await maybe_awaitable(self._progress_callback(self.execution_id, task_id, task_name, value))

    def set_progress_callback(self, callback: Callable) -> Self:
        self._progress_callback = callback
        return self

    def _await_approval(self, task_call_id: str, snapshot):
        """Suspension primitive for approval-gated task calls.

        Maps an ``ApprovalSnapshot`` (fetched by the gate through the
        transport-appropriate approval store — local DB inline, server API
        on workers, parent pipe in runner children) to a verdict: returns,
        raises ``PauseRequested`` (workflow pauses), or raises
        ``ApprovalRejected``.

        Symmetric with ``ctx.resume()`` / ``ctx.pause()`` — the row state
        determines behaviour. First-call-vs-replay is implicit because the
        engine simply re-checks whether the row has reached a terminal state.
        """
        from flux.approvals import ApprovalRejected, ApprovalVerdict
        from flux.errors import PauseRequested
        from flux.models import ApprovalStatus

        if snapshot is None:
            raise PauseRequested(name=f"approval:{task_call_id}")
        if snapshot.status == ApprovalStatus.PENDING.value:
            raise PauseRequested(
                name=f"approval:{task_call_id}",
                output={
                    "type": "approval_required",
                    "execution_id": self.execution_id,
                    "task_call_id": task_call_id,
                    "task_name": snapshot.task_name,
                    "workflow_namespace": snapshot.workflow_namespace,
                    "workflow_name": snapshot.workflow_name,
                    "approval_id": snapshot.id,
                    "requested_at": snapshot.requested_at,
                },
            )
        if snapshot.status == ApprovalStatus.CANCELLED.value:
            return ApprovalVerdict(approved=False, cancelled=True)
        if snapshot.status == ApprovalStatus.REJECTED.value:
            raise ApprovalRejected(
                task_name=(
                    f"{snapshot.workflow_namespace}/{snapshot.workflow_name}/{snapshot.task_name}"
                ),
                approver_subject=snapshot.approver_subject,
                approver_provider=snapshot.approver_provider,
                reason=snapshot.reason,
            )
        return ApprovalVerdict(
            approved=True,
            approver_subject=snapshot.approver_subject,
            approver_provider=snapshot.approver_provider,
            reason=snapshot.reason,
        )

    @property
    def exec_token(self) -> str | None:
        return self._exec_token

    def set_exec_token(self, token: str | None) -> None:
        self._exec_token = token

    def __getstate__(self):
        state = self.__dict__.copy()
        state["_checkpoint"] = None
        state["_progress_callback"] = None
        state.pop("_exec_token", None)
        # Runtime-only: a rebuilt context derives occurrences afresh as the
        # replay re-enters each task call in program order.
        state.pop("_task_occurrences", None)
        return state

    def __setstate__(self, state):
        self.__dict__.update(state)
        if self._checkpoint is None:
            self._checkpoint = lambda _: maybe_awaitable(None)
        if self._progress_callback is None:
            self._progress_callback = lambda *_: None
        if not hasattr(self, "_exec_token"):
            self._exec_token = None
        if not hasattr(self, "_task_occurrences"):
            self._task_occurrences = {}

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
        ctx: ExecutionContext = ExecutionContext(
            workflow_id=data["workflow_id"],
            workflow_namespace=data.get("workflow_namespace", "default"),
            workflow_name=data["workflow_name"],
            input=data["input"],
            execution_id=data["execution_id"],
            state=data["state"],
            current_worker=data.get("current_worker"),
            events=[ExecutionEvent(**event) for event in data["events"]],
            checkpoint=checkpoint,
        )
        return ctx

    def _last_workflow_event(self) -> ExecutionEvent | None:
        """The most recent workflow-lifecycle (``WORKFLOW_*``) event, if any.

        Lifecycle flags must key off this rather than ``events[-1]``: a
        still-running ``parallel()`` sibling can append its TASK_COMPLETED
        *after* WORKFLOW_FAILED/WORKFLOW_PAUSED lands (gather does not cancel
        siblings), and that interleaved task event must not flip a finished
        or paused execution back to "running".
        """
        for event in reversed(self.events):
            # Wire-rebuilt events (from_json) carry the type as a plain str;
            # ExecutionEventType is a str-enum with value == name, so both
            # forms stringify to the same "WORKFLOW_*" token.
            event_type = event.type
            type_name = (
                event_type.value if isinstance(event_type, ExecutionEventType) else str(event_type)
            )
            if type_name.startswith("WORKFLOW_"):
                return event
        return None

    def _is_last_event(self, event_type: ExecutionEventType) -> bool:
        """Check whether the most recent workflow-lifecycle event matches
        ``event_type`` (interleaved task events are ignored — see
        ``_last_workflow_event``)."""
        last = self._last_workflow_event()
        return last is not None and last.type == event_type
