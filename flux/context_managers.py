from __future__ import annotations

from abc import ABC
from abc import abstractmethod

from sqlalchemy.exc import IntegrityError

from flux.context import WorkflowExecutionContext
from flux.errors import ExecutionContextNotFoundError
from flux.models import ExecutionEventModel
from flux.models import SQLiteRepository
from flux.models import WorkflowExecutionContextModel


class ContextManager(ABC):
    @abstractmethod
    def save(self, ctx: WorkflowExecutionContext):  # pragma: no cover
        raise NotImplementedError()

    @abstractmethod
    def get(self, execution_id: str | None) -> WorkflowExecutionContext | None:  # pragma: no cover
        raise NotImplementedError()

    @staticmethod
    def default() -> ContextManager:
        return SQLiteContextManager()


class SQLiteContextManager(ContextManager, SQLiteRepository):
    def __init__(self):
        super().__init__()

    def save(self, ctx: WorkflowExecutionContext):
        with self.session() as session:
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
            except IntegrityError:  # pragma: no cover
                session.rollback()
                raise

    def get(self, execution_id: str | None) -> WorkflowExecutionContext | None:
        if not execution_id:
            return None

        with self.session() as session:
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
