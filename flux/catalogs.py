import importlib
import sys
from typing import Callable, Self
from abc import ABC, abstractmethod

import flux.decorators as d
from flux.exceptions import WorkflowCatalogException, WorkflowNotFoundException


# TODO: add catalog backed by database
class WorkflowCatalog(ABC):

    @abstractmethod
    def get(self, name: str) -> Callable:
        raise NotImplementedError()

    @staticmethod
    def create(options: dict[str, any]) -> Self:
        return ModuleWorkflowCatalog(options)


class ModuleWorkflowCatalog(WorkflowCatalog):

    def __init__(self, options: dict[str, any]):
        if "module" in options:
            self._module = importlib.import_module(options["module"])
        else:
            self._module = sys.modules["__main__"]

    def get(self, name: str) -> Callable:

        w = getattr(self._module, name)
        if not w or not d.workflow.is_workflow(w):
            raise WorkflowNotFoundException(name)
        return w
