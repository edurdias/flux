from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel
from pydantic import field_validator

from flux.domain.events import ExecutionEventType
from flux.domain.events import ExecutionState


class PydanticExecutionEvent(BaseModel):
    """
    Pydantic model for ExecutionEvent that handles automatic datetime conversion.
    """

    id: str | None = None
    type: str  # Using str instead of ExecutionEventType for serialization compatibility
    source_id: str
    name: str
    value: Any = None
    time: datetime

    @field_validator("time", mode="before")
    def parse_datetime(cls, value):
        """Convert string datetime to Python datetime object"""
        if isinstance(value, str):
            return datetime.fromisoformat(value)
        return value

    class Config:
        arbitrary_types_allowed = True


class PydanticWorkflowExecutionContext(BaseModel):
    """
    Pydantic model for WorkflowExecutionContext that handles automatic datetime conversion
    in nested ExecutionEvent objects.
    """

    name: str
    execution_id: str
    input: Any = None
    output: Any = None
    state: str  # Using str instead of ExecutionState for serialization compatibility
    events: list[PydanticExecutionEvent] = []

    class Config:
        arbitrary_types_allowed = True

    def to_domain_model(self):
        """
        Convert the Pydantic model to a domain WorkflowExecutionContext object.

        Returns:
            WorkflowExecutionContext: The converted execution context.
        """
        # Import here to avoid circular imports
        from flux import ExecutionContext
        from flux.domain.events import ExecutionEvent

        # Convert the model events to ExecutionEvent objects
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

        return ExecutionContext(
            name=self.name,
            input=self.input,
            execution_id=self.execution_id,
            state=ExecutionState(self.state),
            events=events,
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PydanticWorkflowExecutionContext:
        """
        Create a PydanticWorkflowExecutionContext from a dictionary.

        Args:
            data: Dictionary representation of a WorkflowExecutionContext

        Returns:
            PydanticWorkflowExecutionContext: The created model
        """
        return cls(**data)
