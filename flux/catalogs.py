from abc import ABC, abstractmethod
from typing import Callable

from flux.exceptions import WorkflowNotFoundException

class WorkflowCatalog(ABC):

    @abstractmethod
    def load_workflow(self, name: str) -> Callable:
        raise NotImplementedError()


class LocalWorkflowCatalog(WorkflowCatalog):

    def __init__(self, functions: dict = globals()):
        self._functions = functions

    def load_workflow(self, name: str) -> Callable:
        workflow = self._functions.get(name)
        if not workflow or not hasattr(workflow, "__is_workflow"):
            raise WorkflowNotFoundException(name)
        return workflow