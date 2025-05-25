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
from flux.domain.resource_request import ResourceRequest
from flux.utils import get_logger

logger = get_logger(__name__)


class WorkflowInfo:
    def __init__(
        self,
        id: str,
        name: str,
        imports: list[str],
        source: bytes,
        version: int = 1,
        requests: ResourceRequest | None = None,
    ):
        self.id = id
        self.name = name
        self.imports = imports
        self.source = source
        self.version = version
        self.requests = requests

    def to_dict(self) -> dict[str, Any]:
        """
        Convert WorkflowInfo to a dictionary representation.

        Returns:
            Dictionary with workflow information
        """
        result = {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "imports": self.imports,
            "source": self.source,
            "requests": {},
        }

        # Convert WorkflowRequests to dict if present
        if self.requests:
            requests_dict = {}
            # Only include non-None attributes
            for attr in ["cpu", "memory", "gpu", "disk", "packages"]:
                value = getattr(self.requests, attr, None)
                if value is not None:
                    requests_dict[attr] = value

            result["requests"] = requests_dict

        return result


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

    def parse(self, source: bytes) -> list[WorkflowInfo]:
        """
        Parse Python source code to extract workflows and their metadata.

        Args:
            source: Python source code as bytes

        Returns:
            A list of WorkflowInfo objects representing the parsed workflows

        Raises:
            SyntaxError: If the source code has invalid syntax or no workflows are found
        """
        try:
            tree = ast.parse(source)

            # Results container
            workflow_infos = []
            imports: list[str] = []

            # Single pass to extract both imports and workflow functions
            for node in ast.walk(tree):
                # Extract imports
                if isinstance(node, ast.Import):
                    imports.extend(name.name for name in node.names)
                elif isinstance(node, ast.ImportFrom):
                    module_prefix = f"{node.module}." if node.module else ""
                    imports.extend(f"{module_prefix}{name.name}" for name in node.names)

                # Extract workflow functions
                elif isinstance(node, ast.AsyncFunctionDef):
                    workflow_name = None
                    workflow_requests = None

                    for decorator in node.decorator_list:
                        # Simple @workflow decorator
                        if (
                            isinstance(decorator, ast.Name)
                            and getattr(decorator, "id", None) == "workflow"
                        ):
                            workflow_name = node.name
                            break

                        # @workflow.with_options decorator
                        elif (
                            isinstance(decorator, ast.Call)
                            and isinstance(decorator.func, ast.Attribute)
                            and isinstance(decorator.func.value, ast.Name)
                            and decorator.func.value.id == "workflow"
                            and decorator.func.attr == "with_options"
                        ):
                            # Extract workflow name and requests from decorator args
                            for kw in decorator.keywords:
                                if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                                    workflow_name = kw.value.value
                                elif kw.arg == "requests":
                                    workflow_requests = self._extract_workflow_requests(kw.value)

                            if not workflow_name:
                                workflow_name = node.name

                            break

                    if workflow_name:
                        workflow_infos.append(
                            WorkflowInfo(
                                id=workflow_name,
                                name=workflow_name,
                                imports=imports,
                                source=source,
                                requests=workflow_requests,
                            ),
                        )

            if not workflow_infos:
                raise SyntaxError("No workflow found in the provided code.")

            return workflow_infos

        except SyntaxError as e:
            raise SyntaxError(f"Invalid syntax: {e.msg}")
        except Exception as e:
            raise SyntaxError(f"Error parsing source code: {str(e)}")

    def _extract_workflow_requests(self, node: ast.AST) -> ResourceRequest | None:
        """
        Extract workflow requests from an AST node.

        Args:
            node: AST node representing a WorkflowRequests expression

        Returns:
            WorkflowRequests object if successfully extracted, None otherwise
        """
        cpu = None
        memory = None
        gpu = None
        disk = None
        packages = None

        # Helper to safely extract constant value
        def get_constant_value(node: ast.AST) -> Any:
            return node.value if isinstance(node, ast.Constant) else None

        # Helper to extract list of constant values
        def get_constant_list(node: ast.AST) -> list[str] | None:
            if not isinstance(node, ast.List):
                return None
            return [elt.value for elt in node.elts if isinstance(elt, ast.Constant)]

        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == ResourceRequest.__name__:
                for kw in node.keywords:
                    if kw.arg == "cpu":
                        cpu = get_constant_value(kw.value)
                    elif kw.arg == "memory":
                        memory = get_constant_value(kw.value)
                    elif kw.arg == "gpu":
                        gpu = get_constant_value(kw.value)
                    elif kw.arg == "disk":
                        disk = get_constant_value(kw.value)
                    elif kw.arg == "packages":
                        packages = get_constant_list(kw.value)

            elif (
                isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == ResourceRequest.__name__
                and node.args  # Ensure there are arguments
            ):
                method = node.func.attr

                # Handle each factory method
                if method.startswith("with_"):
                    resource_type = method[5:]  # Remove 'with_' prefix
                    if resource_type == "packages":
                        packages = get_constant_list(node.args[0])
                    elif resource_type == "cpu" and node.args:
                        cpu = get_constant_value(node.args[0])
                    elif resource_type == "memory" and node.args:
                        memory = get_constant_value(node.args[0])
                    elif resource_type == "gpu" and node.args:
                        gpu = get_constant_value(node.args[0])
                    elif resource_type == "disk" and node.args:
                        disk = get_constant_value(node.args[0])

        # Create and return a WorkflowRequests object with the extracted values
        if any(param is not None for param in [cpu, memory, gpu, disk, packages]):
            return ResourceRequest(cpu=cpu, memory=memory, gpu=gpu, disk=disk, packages=packages)

        return None

    @staticmethod
    def create() -> WorkflowCatalog:
        return SQLiteWorkflowCatalog()


class SQLiteWorkflowCatalog(WorkflowCatalog, SQLiteRepository):
    def all(self) -> list[WorkflowInfo]:
        """
        Get all workflows in the catalog (latest version of each).

        Returns:
            List of WorkflowInfo objects
        """
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

            # Join with the original table to get complete records with the latest version
            models = (
                session.query(WorkflowModel)
                .join(
                    subq,
                    and_(
                        WorkflowModel.name == subq.c.name,
                        WorkflowModel.version == subq.c.max_version,
                    ),
                )
                .order_by(WorkflowModel.name)
                .all()
            )

            workflows = []
            for model in models:
                # Convert requests dictionary to WorkflowRequests object if present
                requests = None
                if model.requests:
                    requests = ResourceRequest(
                        cpu=model.requests.get("cpu"),
                        memory=model.requests.get("memory"),
                        gpu=model.requests.get("gpu"),
                        disk=model.requests.get("disk"),
                        packages=model.requests.get("packages"),
                    )

                workflows.append(
                    WorkflowInfo(
                        id=model.id,
                        name=model.name,
                        imports=model.imports,
                        source=model.source,
                        version=model.version,
                        requests=requests,
                    ),
                )

            return workflows

    def get(self, name: str, version: int | None = None) -> WorkflowInfo:
        """
        Retrieve a workflow by name and optionally version.

        Args:
            name: Name of the workflow to retrieve
            version: Optional specific version to retrieve (retrieves latest if not specified)

        Returns:
            WorkflowInfo object representing the workflow

        Raises:
            WorkflowNotFoundError: If no workflow with the given name/version is found
        """
        model = self._get(name, version)
        if not model:
            raise WorkflowNotFoundError(name)

        # Convert requests dictionary to WorkflowRequests object if present
        requests = None
        if model.requests:
            requests = ResourceRequest(
                cpu=model.requests.get("cpu"),
                memory=model.requests.get("memory"),
                gpu=model.requests.get("gpu"),
                disk=model.requests.get("disk"),
                packages=model.requests.get("packages"),
            )

        return WorkflowInfo(
            id=model.id,
            name=model.name,
            imports=model.imports,
            source=model.source,
            version=model.version,
            requests=requests,
        )

    def save(self, workflows: list[WorkflowInfo]):
        from uuid import uuid4

        with self.session() as session:
            try:
                for workflow in workflows:
                    workflow.id = uuid4().hex
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

                models = query.all()
                logger.debug(
                    f"Deleting {len(models)} workflows with name '{name}' and version '{version}'",
                )
                for model in models:
                    session.delete(model)

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
