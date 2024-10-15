from abc import ABC, abstractmethod
from typing import Callable, Self

from flux.exceptions import WorkflowNotFoundException
import flux.decorators as decorators


# TODO: add catalog backed by database
class WorkflowCatalog(ABC):

    @abstractmethod
    def get(self, name: str) -> Callable:
        raise NotImplementedError()

    @staticmethod
    def default(workflows: dict = globals()) -> Self:
        return LocalWorkflowCatalog(workflows)


class LocalWorkflowCatalog(WorkflowCatalog):

    def __init__(self, workflows: dict = globals()):
        self._workflows = workflows

    def get(self, name: str) -> Callable:
        w = self._workflows.get(name)
        if not w or not decorators.workflow.is_workflow(w):
            raise WorkflowNotFoundException(name)
        return w
