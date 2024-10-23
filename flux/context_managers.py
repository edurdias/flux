from __future__ import annotations

from abc import ABC
from abc import abstractmethod

from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from flux.context import WorkflowExecutionContext
from flux.errors import ExecutionContextNotFoundError
from flux.models import Base
from flux.models import ExecutionEventModel
from flux.models import WorkflowExecutionContextModel


class ContextManager(ABC):
    @abstractmethod
    def save(self, ctx: WorkflowExecutionContext):  # pragma: no cover
        raise NotImplementedError()

    @abstractmethod
    def get(self, execution_id: str) -> WorkflowExecutionContext:  # pragma: no cover
        raise NotImplementedError()

    @staticmethod
    def default() -> ContextManager:
        return SQLiteContextManager()


class SQLiteContextManager(ContextManager):
    max_attempts = 10

    def __init__(self, db_path: str = ".data"):
        self._engine = create_engine(
            f"sqlite:///{db_path}/flux.db",
            echo=False,
        )
        Base.metadata.create_all(self._engine)

    def save(self, ctx: WorkflowExecutionContext):
        with Session(self._engine) as session:
            try:
                context = session.get(
                    WorkflowExecutionContextModel,
                    ctx.execution_id,
                )
                if context:
                    context.output = ctx.output
                    additional_events = self._get_additional_events(
                        ctx,
                        context,
                    )
                    context.events.extend(additional_events)
                else:
                    session.add(WorkflowExecutionContextModel.from_plain(ctx))
                session.commit()
            except IntegrityError:
                session.rollback()
                raise

    def get(self, execution_id: str) -> WorkflowExecutionContext:
        with Session(self._engine) as session:
            context = session.get(WorkflowExecutionContextModel, execution_id)
            if context:
                return context.to_plain()
            raise ExecutionContextNotFoundError(execution_id)

    def _get_additional_events(self, ctx, context):
        existing_events = [(e.event_id, e.type) for e in context.events]
        return [
            ExecutionEventModel.from_plain(ctx.execution_id, e)
            for e in ctx.events
            if (e.id, e.type) not in existing_events
        ]
