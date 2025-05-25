from __future__ import annotations

from abc import ABC
from abc import abstractmethod

from sqlalchemy.exc import IntegrityError

from flux import ExecutionContext
from flux.domain.events import ExecutionState
from flux.errors import ExecutionContextNotFoundError
from flux.models import ExecutionEventModel
from flux.models import SQLiteRepository
from flux.models import ExecutionContextModel
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
            # Find any context that's in CREATED state
            query = (
                session.query(ExecutionContextModel)
                .filter(
                    ExecutionContextModel.state == ExecutionState.CREATED,
                )
                .with_for_update(skip_locked=True)
            )

            # Try to get a context that matches the worker's resources first
            matching_model = None
            for model in query.all():
                ctx = model.to_plain()

                # If there are resource requests, check if the worker can fulfill them
                if ctx.requests:
                    if ctx.requests.matches_worker(worker.resources, worker.packages):
                        matching_model = model
                        break
                else:
                    # If no resource requests, any worker will do
                    matching_model = model
                    break

            if matching_model:
                ctx = matching_model.to_plain()
                ctx.schedule(worker)
                matching_model.state = ctx.state
                matching_model.events.extend(self._get_additional_events(ctx, matching_model))
                session.commit()
                return ctx

            return None

    def claim(self, execution_id: str, worker: WorkerInfo) -> ExecutionContext:
        with self.session() as session:
            model = session.get(ExecutionContextModel, execution_id)
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
        model: ExecutionContextModel,
    ):
        existing_events = [(e.event_id, e.type) for e in model.events]
        return [
            ExecutionEventModel.from_plain(ctx.execution_id, e)
            for e in ctx.events
            if (e.id, e.type) not in existing_events
        ]
