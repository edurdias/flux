import pickle
from abc import ABC, abstractmethod
from flux.context import WorkflowExecutionContext


class ContextManager(ABC):

    @abstractmethod
    def save_context(self, ctx: WorkflowExecutionContext):
        raise NotImplementedError()

    @abstractmethod
    def get_context(self, execution_id: str) -> WorkflowExecutionContext:
        raise NotImplementedError()


class InMemoryContextManager(ContextManager):

    def save_context(self, ctx: WorkflowExecutionContext):
        self.state[f"{ctx.execution_id}"] = ctx

    def get_context(self, execution_id: str) -> WorkflowExecutionContext:
        return self.state[f"{execution_id}"]


class LocalFileContextManager(ContextManager):

    state: dict[str, WorkflowExecutionContext] = {}

    def save_context(self, ctx: WorkflowExecutionContext):
        with open(f"./dill/{ctx.execution_id}.pkl", "wb") as f:
            pickle.dump(ctx, f)

    def get_context(self, execution_id: str) -> WorkflowExecutionContext:
        with open(f"./dill/{execution_id}.pkl", "rb") as f:
            return pickle.load(f)
