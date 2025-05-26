from __future__ import annotations

from abc import ABC
from abc import abstractmethod

from sqlalchemy.exc import IntegrityError

from flux import ExecutionContext
from flux.domain import ExecutionState
from flux.domain import ResourceRequest
from flux.errors import ExecutionContextNotFoundError
from flux.models import ExecutionEventModel
from flux.models import SQLiteRepository
from flux.models import ExecutionContextModel
from flux.models import WorkflowModel
from flux.worker_registry import WorkerInfo


class ContextManager(ABC):
    @abstractmethod
    def save(self, ctx: ExecutionContext) -> ExecutionContext:  # pragma: no cover
        raise NotImplementedError()

    @abstractmethod
    def get(self, execution_id: str | None) -> ExecutionContext:  # pragma: no cover
        raise NotImplementedError()

    @abstractmethod
    def next_execution(
        self,
        worker: WorkerInfo,
    ) -> ExecutionContext | None:  # pragma: no cover
        raise NotImplementedError()

    @abstractmethod
    def next_cancellation(
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
            model = session.get(ExecutionContextModel, execution_id)
            if model:
                return model.to_plain()
            raise ExecutionContextNotFoundError(execution_id)

    def save(self, ctx: ExecutionContext) -> ExecutionContext:
        with self.session() as session:
            try:
                model = session.get(
                    ExecutionContextModel,
                    ctx.execution_id,
                )
                if model:
                    model.output = ctx.output
                    model.state = ctx.state
                    model.events.extend(self._get_additional_events(ctx, model))
                else:
                    session.add(ExecutionContextModel.from_plain(ctx))
                session.commit()
                return self.get(ctx.execution_id)
            except IntegrityError:  # pragma: no cover
                session.rollback()
                raise

    def next_execution(self, worker: WorkerInfo) -> ExecutionContext | None:
        with self.session() as session:
            model, workflow = self._next_execution_with_requests(worker, session)

            if not model or not workflow:
                model, workflow = self._next_execution_without_requests(session)

            if model and workflow:
                ctx = model.to_plain()
                ctx.schedule(worker)
                model.state = ctx.state
                model.events.extend(self._get_additional_events(ctx, model))
                session.commit()
                return ctx

            return None

    def next_cancellation(self, worker: WorkerInfo) -> ExecutionContext | None:
        with self.session() as session:
            query = (
                session.query(ExecutionContextModel)
                .filter(
                    ExecutionContextModel.state == ExecutionState.CANCELLING,
                    ExecutionContextModel.worker_name == worker.name,
                )
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            model = query.first()
            if model:
                return model.to_plain()
            return None

    def _next_execution_without_requests(self, session):
        no_requests_query = (
            session.query(ExecutionContextModel, WorkflowModel)
            .join(WorkflowModel)
            .filter(
                ExecutionContextModel.state == ExecutionState.CREATED,
                WorkflowModel.requests.is_(None),
            )
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        result = no_requests_query.first()
        if result:
            return result
        return None, None

    def _next_execution_with_requests(self, worker, session):
        with_requests_query = (
            session.query(ExecutionContextModel, WorkflowModel)
            .join(WorkflowModel)
            .filter(
                ExecutionContextModel.state == ExecutionState.CREATED,
                WorkflowModel.requests.is_not(None),
            )
            .with_for_update(skip_locked=True)
        )
        for model, workflow in with_requests_query.all():
            requests = workflow.requests if workflow else None
            requests = ResourceRequest(**(requests or {}))
            if requests.matches_worker(worker.resources, worker.packages):
                return model, workflow
        return None, None

    def claim(self, execution_id: str, worker: WorkerInfo) -> ExecutionContext:
        with self.session() as session:
            model = session.get(ExecutionContextModel, execution_id)
            if model:
                ctx = model.to_plain()
                ctx.claim(worker)
                model.state = ctx.state
                model.worker_name = ctx.current_worker
                model.events.extend(self._get_additional_events(ctx, model))
                session.commit()
                return ctx
            raise ExecutionContextNotFoundError(execution_id)

    def _get_additional_events(
        self,
        ctx: ExecutionContext,
        model: ExecutionContextModel,
    ):
        existing_events = [(e.event_id, e.type) for e in model.events]
        return [
            ExecutionEventModel.from_plain(ctx.execution_id, e)
            for e in ctx.events
            if (e.id, e.type) not in existing_events
        ]
