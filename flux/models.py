from datetime import datetime
import json
from typing import Self
from sqlalchemy import (
    Column,
    Date,
    DateTime,
    Integer,
    PrimaryKeyConstraint,
    String,
    JSON,
    ForeignKey,
    Enum as SqlEnum,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import relationship, declarative_base
from flux.context import WorkflowExecutionContext
from flux.encoders import WorkflowContextEncoder
from flux.events import ExecutionEvent, ExecutionEventType


Base = declarative_base()


class WorkflowExecutionContextModel(Base):
    __tablename__ = "workflow_executions"

    execution_id = Column(
        String,
        primary_key=True,
        unique=True,
        nullable=False,
    )
    name = Column(String, nullable=False)
    input = Column(JSON, nullable=False)  # Assuming input can be serialized as JSON
    output = Column(JSON, nullable=True)  # Assuming input can be serialized as JSON

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
        input: any,
        events: list["ExecutionEventModel"] = [],
        output: any = None,
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
    def from_plain(cls, obj: WorkflowExecutionContext) -> Self:
        return cls(
            execution_id=obj.execution_id,
            name=obj.name,
            input=obj.input,
            output=obj.output,
            events=[
                ExecutionEventModel.from_plain(obj.execution_id, e) for e in obj.events
            ],
        )


class ExecutionEventModel(Base):
    __tablename__ = "workflow_execution_events"

    execution_id = Column(
        String, ForeignKey("workflow_executions.execution_id"), nullable=False
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_id = Column(String, nullable=False)
    event_id = Column(String, nullable=False, unique=True)
    type = Column(SqlEnum(ExecutionEventType), nullable=False)
    name = Column(String, nullable=False)
    value = Column(JSON, nullable=True)
    time = Column(DateTime, nullable=False)
    execution = relationship("WorkflowExecutionContextModel", back_populates="events")

    def __init__(
        self,
        source_id: str,
        event_id: str,
        execution_id: str,
        type: ExecutionEventType,
        name: str,
        time: datetime,
        value: any = None,
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
            value=self.value,
            time=self.time,
        )

    @classmethod
    def from_plain(cls, execution_id: str, obj: ExecutionEvent) -> Self:
        return cls(
            execution_id=execution_id,
            source_id=obj.source_id,
            event_id=obj.id,
            type=obj.type,
            name=obj.name,
            time=obj.time,
            value=obj.value,
        )


@event.listens_for(ExecutionEventModel, "before_insert")
def ExecutionEventModel_before_insert(mapper, connection, target: ExecutionEventModel):
    target.value = json.loads(json.dumps(target.value, cls=WorkflowContextEncoder))


@event.listens_for(ExecutionEventModel, "before_update")
def ExecutionEventModel_before_update(mapper, connection, target: ExecutionEventModel):
    target.value = json.loads(json.dumps(target.value, cls=WorkflowContextEncoder))


@event.listens_for(WorkflowExecutionContextModel, "before_insert")
def WorkflowExecutionContextModel_before_insert(
    mapper, connection, target: WorkflowExecutionContextModel
):
    target.output = json.loads(json.dumps(target.output, cls=WorkflowContextEncoder))


@event.listens_for(WorkflowExecutionContextModel, "before_update")
def WorkflowExecutionContextModel_before_update(
    mapper, connection, target: WorkflowExecutionContextModel
):
    target.output = json.loads(json.dumps(target.output, cls=WorkflowContextEncoder))
