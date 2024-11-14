from __future__ import annotations

from abc import ABC
from abc import abstractmethod
from typing import Any

from sqlalchemy import desc
from sqlalchemy.exc import IntegrityError

import flux.decorators as decorators
from flux.config import Configuration
from flux.errors import WorkflowNotFoundError
from flux.models import SQLiteRepository
from flux.models import WorkflowModel
from flux.utils import import_module
from flux.utils import import_module_from_file


class WorkflowCatalog(ABC):
    @abstractmethod
    def all(self) -> list[WorkflowModel]:  # pragma: no cover
        raise NotImplementedError()

    @abstractmethod
    def get(self, name: str, version: int | None = None) -> WorkflowModel:  # pragma: no cover
        raise NotImplementedError()

    @abstractmethod
    def save(self, workflow: decorators.workflow):  # pragma: no cover
        raise NotImplementedError()

    @abstractmethod
    def delete(self, name: str, version: int | None = None):  # pragma: no cover
        raise NotImplementedError()

    @staticmethod
    def create(options: dict[str, Any] | None = None) -> WorkflowCatalog:
        return SQLiteWorkflowCatalog(options)


class SQLiteWorkflowCatalog(WorkflowCatalog, SQLiteRepository):
    def __init__(self, options: dict[str, Any] | None = None):
        super().__init__()
        settings = Configuration.get().settings
        if settings.catalog.auto_register:
            options = options or {}
            self._auto_register_workflows({**settings.catalog.options, **options})

    def all(self) -> list[WorkflowModel]:
        with self.session() as session:
            return [
                model
                for model in session.query(WorkflowModel).order_by(
                    WorkflowModel.name,
                    desc(WorkflowModel.version),
                )
            ]

    def get(self, name: str, version: int | None = None) -> WorkflowModel:
        model = self._get(name, version)
        if not model:
            raise WorkflowNotFoundError(name)
        return model

    def save(self, workflow: decorators.workflow):
        with self.session() as session:
            try:
                name = workflow.name
                existing_model = self._get(name)
                version = existing_model.version + 1 if existing_model else 1
                session.add(WorkflowModel(name, workflow, version))
                session.commit()
            except IntegrityError:  # pragma: no cover
                session.rollback()
                raise

    def delete(self, name: str, version: int | None = None):  # pragma: no cover
        with self.session() as session:
            try:
                query = session.query(WorkflowModel).filter(WorkflowModel.name == name)

                if version:
                    query = query.filter(WorkflowModel.version == version)

                query.delete()
                session.commit()
            except IntegrityError:  # pragma: no cover
                session.rollback()
                raise

    def _get(self, name: str, version: int | None = None) -> WorkflowModel:
        with self.session() as session:
            query = session.query(WorkflowModel).filter(WorkflowModel.name == name)

            if version:
                return query.filter(WorkflowModel.version == version).first()

            return query.order_by(desc(WorkflowModel.version)).first()

    def _auto_register_workflows(self, options: dict[str, Any]):
        module = (
            import_module(options["module"])
            if "module" in options
            else import_module_from_file(options["path"])
        )

        if not module:
            return

        for name in dir(module):
            workflow = getattr(module, name)
            if decorators.workflow.is_workflow(workflow):
                self.save(workflow)
