from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel
from pydantic import field_validator

from flux import domain


class ExecutionEvent(BaseModel):
    id: str | None = None
    type: str
    source_id: str
    name: str
    value: Any = None
    time: datetime

    @field_validator("time", mode="before")
    def parse_datetime(cls, value):
        if isinstance(value, str):
            return datetime.fromisoformat(value)
        return value

    class Config:
        arbitrary_types_allowed = True


class ExecutionContext(BaseModel):
    workflow_id: str
    workflow_name: str
    execution_id: str
    input: Any = None
    output: Any = None
    state: str
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
                ),
            )

        return domain.ExecutionContext(
            workflow_id=self.workflow_id,
            workflow_name=self.workflow_name,
            input=self.input,
            execution_id=self.execution_id,
            state=ExecutionState(self.state),
            events=events,
        )

    @classmethod
    def from_domain(cls, ctx: domain.ExecutionContext) -> ExecutionContext:
        return cls(
            workflow_id=ctx.workflow_id,
            workflow_name=ctx.workflow_name,
            execution_id=ctx.execution_id,
            input=ctx.input,
            state=ctx.state.value,
            output=ctx.output,
            events=[
                ExecutionEvent(
                    id=event.id,
                    type=event.type.value,
                    source_id=event.source_id,
                    name=event.name,
                    value=event.value,
                    time=event.time,
                )
                for event in ctx.events
            ],
        )

    def summary(self) -> dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "workflow_name": self.workflow_name,
            "execution_id": self.execution_id,
            "input": self.input,
            "output": self.output,
            "state": self.state,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExecutionContext:
        return cls(**data)
