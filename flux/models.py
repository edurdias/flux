from __future__ import annotations

from datetime import datetime
from typing import Any

import dill
from sqlalchemy import Column
from sqlalchemy import DateTime
from sqlalchemy import Enum as SqlEnum
from sqlalchemy import ForeignKey
from sqlalchemy import Integer
from sqlalchemy import PickleType
from sqlalchemy import String
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.orm import relationship

from flux.context import WorkflowExecutionContext
from flux.events import ExecutionEvent
from flux.events import ExecutionEventType


class Base(DeclarativeBase):
    pass


class WorkflowExecutionContextModel(Base):
    __tablename__ = "workflow_executions"

    execution_id = Column(
        String,
        primary_key=True,
        unique=True,
        nullable=False,
    )
    name = Column(String, nullable=False)
    input = Column(PickleType(pickler=dill), nullable=True)
    output = Column(PickleType(pickler=dill), nullable=True)

    # Relationship to events
    events = relationship(
        "ExecutionEventModel",
        back_populates="execution",
        cascade="all, delete-orphan",
        order_by="ExecutionEventModel.id",
    )

    def __init__(
        self,
        execution_id: str,
        name: str,
        input: Any,
        events: list[ExecutionEventModel] = [],
        output: Any | None = None,
    ):
        self.execution_id = execution_id
        self.name = name
        self.input = input
        self.events = events
        self.output = output

    def to_plain(self) -> WorkflowExecutionContext:
        return WorkflowExecutionContext(
            self.name,
            self.input,
            self.execution_id,
            [e.to_plain() for e in self.events],
        )

    @classmethod
    def from_plain(cls, obj: WorkflowExecutionContext) -> WorkflowExecutionContextModel:
        return cls(
            execution_id=obj.execution_id,
            name=obj.name,
            input=obj.input,
            output=obj.output,
            events=[ExecutionEventModel.from_plain(obj.execution_id, e) for e in obj.events],
        )


class ExecutionEventModel(Base):
    __tablename__ = "workflow_execution_events"

    execution_id = Column(
        String,
        ForeignKey("workflow_executions.execution_id"),
        nullable=False,
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_id = Column(String, nullable=False)
    event_id = Column(String, nullable=False)
    type = Column(SqlEnum(ExecutionEventType), nullable=False)
    name = Column(String, nullable=False)
    value = Column(PickleType(pickler=dill), nullable=True)
    time = Column(DateTime, nullable=False)
    execution = relationship(
        "WorkflowExecutionContextModel",
        back_populates="events",
    )

    def __init__(
        self,
        source_id: str,
        event_id: str,
        execution_id: str,
        type: ExecutionEventType,
        name: str,
        time: datetime,
        value: Any | None = None,
    ):
        self.source_id = source_id
        self.event_id = event_id
        self.execution_id = execution_id
        self.type = type
        self.name = name
        self.time = time
        self.value = value

    def to_plain(self) -> ExecutionEvent:
        return ExecutionEvent(
            type=self.type,
            id=self.event_id,
            source_id=self.source_id,
            name=self.name,
            time=self.time,
            value=self.value,
        )

    @classmethod
    def from_plain(cls, execution_id: str, obj: ExecutionEvent) -> ExecutionEventModel:
        return cls(
            execution_id=execution_id,
            source_id=obj.source_id,
            event_id=obj.id,
            type=obj.type,
            name=obj.name,
            time=obj.time,
            value=obj.value,
        )
