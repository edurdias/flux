from abc import ABC, abstractmethod
from typing import Callable

from flux.exceptions import WorkflowNotFoundException


class WorkflowLoader(ABC):

    @abstractmethod
    def load_workflow(self, name: str) -> Callable:
        raise NotImplementedError()


class LocalFunctionWorkflowLoader(WorkflowLoader):

    def load_workflow(self, name: str) -> Callable:
        workflow = globals().get(name)
        if not workflow or not hasattr(workflow, "__is_workflow"):
            raise WorkflowNotFoundException(name)
        return workflow
