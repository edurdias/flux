from __future__ import annotations

from abc import ABC
from abc import abstractmethod

from sqlalchemy.exc import IntegrityError

from flux import ExecutionContext
from flux.domain.events import ExecutionState
from flux.errors import ExecutionContextNotFoundError
from flux.models import ExecutionEventModel
from flux.models import SQLiteRepository
from flux.models import WorkflowExecutionContextModel
from flux.worker_registry import WorkerInfo


class ContextManager(ABC):
    @abstractmethod
    def save(self, ctx: ExecutionContext) -> ExecutionContext:  # pragma: no cover
        raise NotImplementedError()

    @abstractmethod
    def get(self, execution_id: str | None) -> ExecutionContext:  # pragma: no cover
        raise NotImplementedError()

    @abstractmethod
    def next_pending_execution(
        self,
        worker: WorkerInfo,
    ) -> ExecutionContext | None:  # pragma: no cover
        raise NotImplementedError()

    @abstractmethod
    def claim(self, execution_id: str, worker: WorkerInfo) -> ExecutionContext:
        raise NotImplementedError()

    @staticmethod
    def create() -> ContextManager:
        return SQLiteContextManager()


class SQLiteContextManager(ContextManager, SQLiteRepository):
    def __init__(self):
        super().__init__()

    def get(self, execution_id: str | None) -> ExecutionContext:
        with self.session() as session:
            model = session.get(WorkflowExecutionContextModel, execution_id)
            if model:
                return model.to_plain()
            raise ExecutionContextNotFoundError(execution_id)

    def save(self, ctx: ExecutionContext) -> ExecutionContext:
        with self.session() as session:
            try:
                model = session.get(
                    WorkflowExecutionContextModel,
                    ctx.execution_id,
                )
                if model:
                    model.output = ctx.output
                    model.state = ctx.state
                    model.events.extend(self._get_additional_events(ctx, model))
                else:
                    session.add(WorkflowExecutionContextModel.from_plain(ctx))
                session.commit()
                return self.get(ctx.execution_id)
            except IntegrityError:  # pragma: no cover
                session.rollback()
                raise

    def next_pending_execution(self, worker: WorkerInfo) -> ExecutionContext | None:
        with self.session() as session:
            model = (
                session.query(WorkflowExecutionContextModel)
                .filter(
                    WorkflowExecutionContextModel.state == ExecutionState.CREATED,
                )
                .with_for_update(skip_locked=True)
                .first()
            )

            if model:
                ctx = model.to_plain()
                ctx.schedule(worker)
                model.state = ctx.state
                model.events.extend(self._get_additional_events(ctx, model))
                session.commit()
                return ctx
            return None

    def claim(self, execution_id: str, worker: WorkerInfo) -> ExecutionContext:
        with self.session() as session:
            model = session.get(WorkflowExecutionContextModel, execution_id)
            if model:
                ctx = model.to_plain()
                ctx.claim(worker)
                model.state = ctx.state
                model.events.extend(self._get_additional_events(ctx, model))
                session.commit()
                return ctx
            raise ExecutionContextNotFoundError(execution_id)

    def _get_additional_events(
        self,
        ctx: ExecutionContext,
        model: WorkflowExecutionContextModel,
    ):
        existing_events = [(e.event_id, e.type) for e in model.events]
        return [
            ExecutionEventModel.from_plain(ctx.execution_id, e)
            for e in ctx.events
            if (e.id, e.type) not in existing_events
        ]
