import pickle
from typing import Self
from abc import ABC, abstractmethod

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from flux.context import WorkflowExecutionContext
from flux.models import ExecutionEventModel, WorkflowExecutionContextModel
from flux.models import Base


class ContextManager(ABC):

    @abstractmethod
    def save(self, ctx: WorkflowExecutionContext):
        raise NotImplementedError()

    @abstractmethod
    def get(self, execution_id: str) -> WorkflowExecutionContext:
        raise NotImplementedError()

    def default() -> Self:
        return SQLiteContextManager()


class InMemoryContextManager(ContextManager):

    def __init__(self):
        self._state: dict[str, WorkflowExecutionContext] = {}

    def save(self, ctx: WorkflowExecutionContext):
        self._state[f"{ctx.execution_id}"] = ctx

    def get(self, execution_id: str) -> WorkflowExecutionContext:
        return self._state[f"{execution_id}"]


class LocalFileContextManager(ContextManager):

    def __init__(self, base_path: str = "./"):
        self._base_path = base_path

    def save(self, ctx: WorkflowExecutionContext):
        with open(f"{self._base_path}.dill/{ctx.execution_id}.pkl", "wb+") as f:
            pickle.dump(ctx, f)

    def get(self, execution_id: str) -> WorkflowExecutionContext:
        with open(f"{self._base_path}.dill/{execution_id}.pkl", "rb+") as f:
            return pickle.load(f)


class SQLiteContextManager(ContextManager):

    max_attempts = 10

    def __init__(self, db_path: str = ".data"):
        self._engine = create_engine(f"sqlite:///{db_path}/flux.db", echo=False)
        Base.metadata.create_all(self._engine)

    def save(self, ctx: WorkflowExecutionContext):
        with Session(self._engine) as session:
            for attempt in range(self.max_attempts):
                try:
                    context = session.query(WorkflowExecutionContextModel).get(
                        ctx.execution_id
                    )
                    if context:
                        context.output = ctx.output
                        additional_events = self._get_additional_events(ctx, context)
                        context.events.extend(additional_events)
                    else:
                        session.add(WorkflowExecutionContextModel.from_plain(ctx))
                    session.commit()
                    break
                except IntegrityError as ex:
                    session.rollback()
                    if attempt >= self.max_attempts:
                        raise

    def get(self, execution_id: str) -> WorkflowExecutionContext:
        with Session(self._engine) as session:
            context = (
                session.query(WorkflowExecutionContextModel)
                .filter_by(execution_id=execution_id)
                .first()
            )
            return context.to_plain() if context else None

    def _get_additional_events(self, ctx, context):
        existing_events = [(e.event_id, e.type) for e in context.events]
        return [
            ExecutionEventModel.from_plain(ctx.execution_id, e)
            for e in ctx.events
            if (e.id, e.type) not in existing_events
        ]
