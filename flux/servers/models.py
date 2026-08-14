from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel
from pydantic import field_validator

from flux import domain
from flux.domain.events import as_utc


class ExecutionEvent(BaseModel):
    id: str | None = None
    type: str
    source_id: str
    name: str
    value: Any = None
    time: datetime
    subject: str | None = None

    @field_validator("time", mode="before")
    def parse_datetime(cls, value):
        # Checkpoints arriving from an older worker carry naive times; reading
        # them as UTC keeps them comparable against the server's own stamps.
        return as_utc(value)

    class Config:
        arbitrary_types_allowed = True


class ExecutionContext(BaseModel):
    workflow_id: str
    workflow_namespace: str
    workflow_name: str
    execution_id: str
    input: Any = None
    output: Any = None
    state: str
    current_worker: str = ""
    # Operator-facing label (executions.name). Optional and defaulted so a
    # payload from an older client still validates.
    name: str | None = None
    events: list[ExecutionEvent] = []

    class Config:
        arbitrary_types_allowed = True

    def to_domain(self) -> domain.ExecutionContext:
        from flux.domain.events import ExecutionEvent, ExecutionEventType, ExecutionState

        events = []
        for event_model in self.events:
            events.append(
                ExecutionEvent(
                    id=event_model.id,
                    type=ExecutionEventType(event_model.type),
                    source_id=event_model.source_id,
                    name=event_model.name,
                    value=event_model.value,
                    time=event_model.time,
                    subject=event_model.subject,
                ),
            )

        return domain.ExecutionContext(
            workflow_id=self.workflow_id,
            workflow_namespace=self.workflow_namespace,
            workflow_name=self.workflow_name,
            input=self.input,
            execution_id=self.execution_id,
            state=ExecutionState(self.state),
            current_worker=self.current_worker or None,
            events=events,
            name=self.name,
        )

    @classmethod
    def from_domain(cls, ctx: domain.ExecutionContext) -> ExecutionContext:
        return cls(
            workflow_id=ctx.workflow_id,
            workflow_namespace=ctx.workflow_namespace,
            workflow_name=ctx.workflow_name,
            execution_id=ctx.execution_id,
            input=ctx.input,
            state=ctx.state.value,
            output=ctx.output,
            current_worker=ctx.current_worker if isinstance(ctx.current_worker, str) else "",
            name=ctx.name,
            events=[
                ExecutionEvent(
                    id=event.id,
                    type=event.type.value,
                    source_id=event.source_id,
                    name=event.name,
                    value=event.value,
                    time=event.time,
                    subject=event.subject,
                )
                for event in ctx.events
            ],
        )

    def summary(self) -> dict[str, Any]:
        output = self.output
        if output is None and self.state == "PAUSED":
            for evt in reversed(self.events):
                if evt.type == "WORKFLOW_PAUSED" and evt.value is not None:
                    v = evt.value
                    if isinstance(v, dict):
                        output = v.get("output")
                    else:
                        output = v
                    break
        return {
            "workflow_id": self.workflow_id,
            "workflow_namespace": self.workflow_namespace,
            "workflow_name": self.workflow_name,
            "execution_id": self.execution_id,
            "input": self.input,
            "output": output,
            "state": self.state,
            "current_worker": self.current_worker,
            "name": self.name,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExecutionContext:
        return cls(**data)


async def redacted_response(ctx: domain.ExecutionContext, *, detailed: bool) -> Any:
    """An execution's wire form with known secret values scrubbed.

    Task outputs are persisted verbatim, so any exit returning this DTO can
    carry a secret.
    """
    from flux.security.redaction import redact_response

    dto = ExecutionContext.from_domain(ctx)
    return await redact_response(dto if detailed else dto.summary())
