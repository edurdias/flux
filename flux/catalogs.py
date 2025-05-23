from __future__ import annotations

import ast
from abc import ABC
from abc import abstractmethod
from typing import Any

from sqlalchemy import and_
from sqlalchemy import desc
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from flux.errors import WorkflowNotFoundError
from flux.models import SQLiteRepository
from flux.models import WorkflowModel


class WorkflowInfo:
    def __init__(self, name: str, imports: list[str], source: bytes, version: int = 1):
        self.name = name
        self.imports = imports
        self.source = source
        self.version = version

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "imports": self.imports,
            "source": self.source,
        }


class WorkflowCatalog(ABC):
    @abstractmethod
    def all(self) -> list[WorkflowInfo]:  # pragma: no cover
        raise NotImplementedError()

    @abstractmethod
    def get(self, name: str, version: int | None = None) -> WorkflowInfo:  # pragma: no cover
        raise NotImplementedError()

    @abstractmethod
    def save(self, workflows: list[WorkflowInfo]):  # pragma: no cover
        raise NotImplementedError()

    @abstractmethod
    def delete(self, name: str, version: int | None = None):  # pragma: no cover
        raise NotImplementedError()

    @abstractmethod
    def parse(self, source: bytes) -> list[WorkflowInfo]:  # pragma: no cover
        raise NotImplementedError()

    @staticmethod
    def create() -> WorkflowCatalog:
        return SQLiteWorkflowCatalog()


class SQLiteWorkflowCatalog(WorkflowCatalog, SQLiteRepository):
    def all(self) -> list[WorkflowInfo]:
        with self.session() as session:
            # Create a subquery that gets the max version for each workflow name
            subq = (
                session.query(
                    WorkflowModel.name.label("name"),
                    func.max(WorkflowModel.version).label("max_version"),
                )
                .group_by(WorkflowModel.name)
                .subquery()
            )

            # Join with the original table to get records with the latest version
            models = (
                session.query(WorkflowModel.name, WorkflowModel.version)
                .join(
                    subq,
                    and_(
                        WorkflowModel.name == subq.c.name,
                        WorkflowModel.version == subq.c.max_version,
                    ),
                )
                .order_by(WorkflowModel.name)
            )

            return [
                WorkflowInfo(
                    model.name,
                    [],  # empty imports
                    b"",  # empty source as bytes
                    model.version,
                )
                for model in models
            ]

    def get(self, name: str, version: int | None = None) -> WorkflowInfo:
        model = self._get(name, version)
        if not model:
            raise WorkflowNotFoundError(name)
        return WorkflowInfo(model.name, model.imports, model.source, model.version)

    def save(self, workflows: list[WorkflowInfo]):
        with self.session() as session:
            try:
                for workflow in workflows:
                    existing_model = self._get(workflow.name)
                    workflow.version = existing_model.version + 1 if existing_model else 1
                    session.add(WorkflowModel(**workflow.to_dict()))
                session.commit()
                return workflows
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

    def parse(self, source: bytes):
        try:
            tree = ast.parse(source)

            workflow_names = []
            imports: list[str] = []

            # Extract imports
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports.extend(name.name for name in node.names)

                elif isinstance(node, ast.ImportFrom):
                    module_prefix = f"{node.module}." if node.module else ""
                    imports.extend(f"{module_prefix}{name.name}" for name in node.names)

                elif isinstance(node, ast.AsyncFunctionDef):
                    for decorator in node.decorator_list:
                        if (
                            isinstance(decorator, ast.Name)
                            and getattr(decorator, "id", None) == "workflow"
                        ):
                            workflow_names.append(node.name)
                            break

            if not workflow_names:
                raise SyntaxError("No workflow found in the provided code.")

            return [WorkflowInfo(name, imports, source) for name in workflow_names]
        except SyntaxError as e:
            raise SyntaxError(f"Invalid syntax: {e.msg}")

    # def _auto_register_workflows(self, options: dict[str, Any]):
    #     module = (
    #         import_module(options["module"])
    #         if "module" in options
    #         else import_module_from_file(options["path"])
    #         if "path" in options
    #         else None
    #     )

    #     if not module:
    #         return

    #     for name in dir(module):
    #         workflow = getattr(module, name)
    #         if isinstance(workflow, decorators.workflow):
    #             self.save(workflow)
