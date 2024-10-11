from abc import ABC, abstractmethod
from typing import Callable

from flux.exceptions import WorkflowNotFoundException
import flux.decorators as decorators


class WorkflowCatalog(ABC):

    @abstractmethod
    def get(self, name: str) -> Callable:
        raise NotImplementedError()


class LocalWorkflowCatalog(WorkflowCatalog):

    def __init__(self, functions: dict = globals()):
        self._functions = functions

    def get(self, name: str) -> Callable:
        w = self._functions.get(name)
        if not w or not decorators.workflow.is_workflow(w):
            raise WorkflowNotFoundException(name)
        return w
