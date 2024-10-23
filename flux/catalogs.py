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
    def get(self, name: str) -> Callable:  # pragma: no cover
        raise NotImplementedError()

    @staticmethod
    def create(options: dict[str, Any] | None = None) -> WorkflowCatalog:
        return ModuleWorkflowCatalog(options)


class ModuleWorkflowCatalog(WorkflowCatalog):
    def __init__(self, options: dict[str, Any] | None = None):
        options = options or {}
        if "module" in options:
            self._module = import_module(options["module"])
        elif "path" in options:
            path = options["path"]
            spec = util.spec_from_file_location("workflow_module", path)
            if spec is None or spec.loader is None:
                raise ImportError(f"Cannot find module at {path}.")
            self._module = util.module_from_spec(spec)
            spec.loader.exec_module(self._module)
        else:
            self._module = sys.modules["__main__"]

    def get(self, name: str) -> Callable:
        workflow = getattr(self._module, name) if hasattr(self._module, name) else None
        if not workflow or not decorators.workflow.is_workflow(workflow):
            raise WorkflowNotFoundError(name, self._module.__name__)
        return workflow
