from __future__ import annotations

import sys
from abc import ABC
from abc import abstractmethod
from importlib import import_module
from importlib import util
from typing import Any
from typing import Callable

import flux.decorators as decorators
from flux.errors import WorkflowNotFoundError


# TODO: add catalog backed by database
class WorkflowCatalog(ABC):
    @abstractmethod
    def get(self, name: str) -> Callable:
        raise NotImplementedError()

    @staticmethod
    def create(options: dict[str, Any] | None = None) -> WorkflowCatalog:
        return ModuleWorkflowCatalog(options)


class ModuleWorkflowCatalog(WorkflowCatalog):
    def __init__(self, options: dict[str, Any] | None = None):
        options = options or {}
        if "module" in options:
            self._module = import_module(options["module"])
        elif "file_path" in options:
            file_path = options["file_path"]
            module_name = "workflow_module"
            spec = util.spec_from_file_location(module_name, file_path)
            if spec is None or spec.loader is None:
                raise ImportError(f"Cannot find module at {file_path}.")
            self._module = util.module_from_spec(spec)
            spec.loader.exec_module(self._module)
        else:
            self._module = sys.modules["__main__"]

    def get(self, name: str) -> Callable:
        w = getattr(self._module, name)
        if not w or not decorators.workflow.is_workflow(w):
            raise WorkflowNotFoundError(name)
        return w
