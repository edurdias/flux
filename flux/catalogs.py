from __future__ import annotations

import sys
from abc import ABC
from abc import abstractmethod
from importlib import import_module
from importlib import util
from pathlib import Path
from typing import Any

from sqlalchemy import desc
from sqlalchemy.exc import IntegrityError

import flux.decorators as decorators
from flux.config import Configuration
from flux.errors import WorkflowNotFoundError
from flux.models import SQLiteRepository
from flux.models import WorkflowModel


class WorkflowCatalog(ABC):
    @abstractmethod
    def get(self, name: str) -> decorators.workflow:  # pragma: no cover
        raise NotImplementedError()

    @abstractmethod
    def save(self, workflow: decorators.workflow):  # pragma: no cover
        raise NotImplementedError()

    @staticmethod
    def create(options: dict[str, Any] | None = None) -> WorkflowCatalog:
        options = {**(options or {}), **Configuration.get().settings.catalog.to_dict()}

        if "type" not in options:
            raise ValueError("Catalog type not specified.")

        catalogs = {
            "module": lambda: ModuleWorkflowCatalog(options),
            "sqlite": lambda: SQLiteWorkflowCatalog(options),
        }

        return catalogs[options["type"]]()


class SQLiteWorkflowCatalog(WorkflowCatalog, SQLiteRepository):
    def __init__(self, options: dict[str, Any] | None = None):
        super().__init__()
        self._load_module_workflows(options or {})

    def get(self, name: str) -> decorators.workflow:
        model = self._get(name)
        if not model:
            raise WorkflowNotFoundError(name)
        return model.code

    def save(self, workflow: decorators.workflow):
        with self.session() as session:
            try:
                name = workflow.name
                model = self._get(name)
                version = model.version + 1 if model else 1
                session.add(WorkflowModel(name, workflow, version))
                session.commit()
            except IntegrityError:  # pragma: no cover
                session.rollback()
                raise

    def _get(self, name: str) -> WorkflowModel:
        with self.session() as session:
            return (
                session.query(WorkflowModel)
                .filter(WorkflowModel.name == name)
                .order_by(desc(WorkflowModel.version))
                .first()
            )

    def _load_module_workflows(self, options: dict[str, Any]):
        module = self._get_module(options)

        if not module:
            return

        for name in dir(module):
            workflow = getattr(module, name)
            if decorators.workflow.is_workflow(workflow):
                self.save(workflow)

    def _get_module(self, options: dict[str, Any]) -> Any:
        module = None
        if "module" in options:
            module = import_module(options["module"])
        elif "path" in options:
            path = Path(options["path"])

            if path.is_dir():
                path = path / "__init__.py"
            elif path.suffix != ".py":
                raise ValueError(f"Invalid module path: {path}")

            spec = util.spec_from_file_location("workflow_module", path)
            if spec is None or spec.loader is None:
                raise ImportError(f"Cannot find module at {path}.")
            module = util.module_from_spec(spec)
            spec.loader.exec_module(module)
        return module


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

    def get(self, name: str) -> decorators.workflow:
        workflow = getattr(self._module, name) if hasattr(self._module, name) else None
        if not workflow or not decorators.workflow.is_workflow(workflow):
            raise WorkflowNotFoundError(name, self._module.__name__)
        return workflow

    def save(self, workflow: decorators.workflow):  # pragma: no cover
        raise NotImplementedError()
