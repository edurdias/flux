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
from flux.models import RepositoryFactory
from flux.models import WorkflowModel
from flux.domain.resource_request import ResourceRequest
from flux.utils import get_logger

logger = get_logger(__name__)

DEFAULT_NAMESPACE = "default"


def resolve_workflow_ref(ref: str | None) -> tuple[str, str]:
    """Parse a user-provided workflow reference into ``(namespace, name)``.

    ``"billing/invoice"`` -> ``("billing", "invoice")``
    ``"hello_world"``     -> ``("default", "hello_world")``
    ``"a/b/c"``           -> ``ValueError`` (flat namespaces only)
    ``""`` or ``None``    -> ``ValueError``
    """
    if not ref:
        raise ValueError("Workflow reference cannot be empty")
    parts = ref.split("/")
    if len(parts) == 1:
        return (DEFAULT_NAMESPACE, parts[0])
    if len(parts) == 2:
        namespace, name = parts
        if not namespace or not name:
            raise ValueError("Workflow reference has empty namespace or name")
        return (namespace, name)
    raise ValueError(
        f"Workflow reference '{ref}' is invalid: flat namespaces only (expected 'name' or 'namespace/name')",
    )


class WorkflowInfo:
    def __init__(
        self,
        id: str,
        name: str,
        imports: list[str],
        source: bytes,
        namespace: str = "default",
        version: int = 1,
        requests: ResourceRequest | None = None,
        schedule: Any | None = None,
        metadata: dict | None = None,
    ):
        self.id = id
        self.namespace = namespace
        self.name = name
        self.imports = imports
        self.source = source
        self.version = version
        self.requests = requests
        self.schedule = schedule
        self.metadata = metadata

    @property
    def qualified_name(self) -> str:
        return f"{self.namespace}/{self.name}"

    def to_dict(self) -> dict[str, Any]:
        result = {
            "id": self.id,
            "namespace": self.namespace,
            "name": self.name,
            "version": self.version,
            "imports": self.imports,
            "source": self.source,
            "requests": {},
            "metadata": self.metadata,
        }

        if self.requests:
            requests_dict = {}
            for attr in ["cpu", "memory", "gpu", "disk", "packages"]:
                value = getattr(self.requests, attr, None)
                if value is not None:
                    requests_dict[attr] = value
            result["requests"] = requests_dict

        return result


class WorkflowCatalog(ABC):
    @abstractmethod
    def all(self, namespace: str | None = None) -> list[WorkflowInfo]:  # pragma: no cover
        raise NotImplementedError()

    @abstractmethod
    def get(
        self,
        namespace: str,
        name: str,
        version: int | None = None,
    ) -> WorkflowInfo:  # pragma: no cover
        raise NotImplementedError()

    @abstractmethod
    def save(self, workflows: list[WorkflowInfo]):  # pragma: no cover
        raise NotImplementedError()

    @abstractmethod
    def delete(
        self,
        namespace: str,
        name: str,
        version: int | None = None,
    ):  # pragma: no cover
        raise NotImplementedError()

    @abstractmethod
    def versions(self, namespace: str, name: str) -> list[WorkflowInfo]:  # pragma: no cover
        raise NotImplementedError()

    @abstractmethod
    def list_namespaces(self) -> list[str]:  # pragma: no cover
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
                        wf_metadata = self._extract_workflow_metadata(node, tree)
                        workflow_infos.append(
                            WorkflowInfo(
                                id=workflow_name,
                                name=workflow_name,
                                namespace="default",
                                imports=imports,
                                source=source,
                                requests=workflow_requests,
                                metadata=wf_metadata,
                            ),
                        )

            if not workflow_infos:
                raise SyntaxError("No workflow found in the provided code.")

            return workflow_infos

        except SyntaxError as e:
            raise SyntaxError(f"Invalid syntax: {e.msg}")
        except Exception as e:
            raise SyntaxError(f"Error parsing source code: {str(e)}")

    def _extract_workflow_metadata(self, func_node: ast.AsyncFunctionDef, tree: ast.Module) -> dict:
        task_func_to_name: dict[str, str] = {}
        exempt_func_to_name: dict[str, str] = {}
        workflow_func_to_name: dict[str, str] = {}

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for dec in node.decorator_list:
                    if isinstance(dec, ast.Name) and dec.id == "task":
                        task_func_to_name[node.name] = node.name
                    elif isinstance(dec, ast.Name) and dec.id == "workflow":
                        workflow_func_to_name[node.name] = node.name
                    elif isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute):
                        if isinstance(dec.func.value, ast.Name):
                            if dec.func.value.id == "task" and dec.func.attr == "with_options":
                                task_name = self._extract_task_name_from_decorator(dec) or node.name
                                if self._is_auth_exempt(dec):
                                    exempt_func_to_name[node.name] = task_name
                                else:
                                    task_func_to_name[node.name] = task_name
                            elif (
                                dec.func.value.id == "workflow" and dec.func.attr == "with_options"
                            ):
                                wf_name = self._extract_task_name_from_decorator(dec) or node.name
                                workflow_func_to_name[node.name] = wf_name

        called_tasks = set()
        called_exempt = set()
        called_workflows = set()

        for node in ast.walk(func_node):
            if isinstance(node, ast.Call):
                func_name = None
                if isinstance(node.func, ast.Name):
                    func_name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    func_name = node.func.attr

                if func_name:
                    if func_name in task_func_to_name:
                        called_tasks.add(task_func_to_name[func_name])
                    elif func_name in exempt_func_to_name:
                        called_exempt.add(exempt_func_to_name[func_name])
                    elif func_name in workflow_func_to_name:
                        called_workflows.add(workflow_func_to_name[func_name])

        return {
            "task_names": sorted(called_tasks),
            "nested_workflows": sorted(called_workflows),
            "auth_exempt_tasks": sorted(called_exempt),
        }

    @staticmethod
    def _extract_task_name_from_decorator(decorator: ast.Call) -> str | None:
        for kw in decorator.keywords:
            if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                return kw.value.value
        return None

    @staticmethod
    def _is_auth_exempt(decorator: ast.Call) -> bool:
        for kw in decorator.keywords:
            if kw.arg == "auth_exempt" and isinstance(kw.value, ast.Constant):
                return bool(kw.value.value)
        return False

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
        return DatabaseWorkflowCatalog()


class DatabaseWorkflowCatalog(WorkflowCatalog):
    def __init__(self):
        # Create repository using factory pattern
        self.repository = RepositoryFactory.create_repository()
        self._engine = self.repository._engine

    def session(self):
        """Delegate to repository session method"""
        return self.repository.session()

    def health_check(self) -> bool:
        """Delegate to repository health check"""
        return self.repository.health_check()

    def all(self, namespace: str | None = None) -> list[WorkflowInfo]:
        with self.session() as session:
            subq = (
                session.query(
                    WorkflowModel.namespace.label("namespace"),
                    WorkflowModel.name.label("name"),
                    func.max(WorkflowModel.version).label("max_version"),
                )
                .group_by(WorkflowModel.namespace, WorkflowModel.name)
                .subquery()
            )

            query = (
                session.query(WorkflowModel)
                .join(
                    subq,
                    and_(
                        WorkflowModel.namespace == subq.c.namespace,
                        WorkflowModel.name == subq.c.name,
                        WorkflowModel.version == subq.c.max_version,
                    ),
                )
                .order_by(WorkflowModel.namespace, WorkflowModel.name)
            )

            if namespace is not None:
                query = query.filter(WorkflowModel.namespace == namespace)

            models = query.all()
            return [self._to_info(m) for m in models]

    def get(self, namespace: str, name: str, version: int | None = None) -> WorkflowInfo:
        model = self._get(namespace, name, version)
        if not model:
            raise WorkflowNotFoundError(f"{namespace}/{name}")
        return self._to_info(model)

    def save(self, workflows: list[WorkflowInfo]):
        from uuid import uuid4

        with self.session() as session:
            try:
                for wf in workflows:
                    wf.id = uuid4().hex
                    existing = self._get(wf.namespace, wf.name)
                    wf.version = existing.version + 1 if existing else 1
                    requests_dict = None
                    if wf.requests is not None:
                        requests_dict = {}
                        for attr in ["cpu", "memory", "gpu", "disk", "packages"]:
                            value = getattr(wf.requests, attr, None)
                            if value is not None:
                                requests_dict[attr] = value
                    model = WorkflowModel(
                        id=wf.id,
                        namespace=wf.namespace,
                        name=wf.name,
                        version=wf.version,
                        imports=wf.imports,
                        source=wf.source,
                        requests=requests_dict,
                        metadata=wf.metadata,
                    )
                    session.add(model)
                session.commit()
                return workflows
            except IntegrityError:  # pragma: no cover
                session.rollback()
                raise

    def delete(self, namespace: str, name: str, version: int | None = None):  # pragma: no cover
        with self.session() as session:
            try:
                query = session.query(WorkflowModel).filter(
                    WorkflowModel.namespace == namespace,
                    WorkflowModel.name == name,
                )
                if version:
                    query = query.filter(WorkflowModel.version == version)
                models = query.all()
                logger.debug(
                    f"Deleting {len(models)} workflows with ref '{namespace}/{name}' version '{version}'",
                )
                for model in models:
                    session.delete(model)
                session.commit()
            except IntegrityError:  # pragma: no cover
                session.rollback()
                raise

    def versions(self, namespace: str, name: str) -> list[WorkflowInfo]:
        with self.session() as session:
            models = (
                session.query(WorkflowModel)
                .filter(
                    WorkflowModel.namespace == namespace,
                    WorkflowModel.name == name,
                )
                .order_by(desc(WorkflowModel.version))
                .all()
            )
            return [self._to_info(m) for m in models]

    def list_namespaces(self) -> list[str]:
        with self.session() as session:
            rows = (
                session.query(WorkflowModel.namespace)
                .distinct()
                .order_by(WorkflowModel.namespace)
                .all()
            )
            return [r[0] for r in rows]

    def _get(
        self,
        namespace: str,
        name: str,
        version: int | None = None,
    ) -> WorkflowModel | None:
        with self.session() as session:
            query = session.query(WorkflowModel).filter(
                WorkflowModel.namespace == namespace,
                WorkflowModel.name == name,
            )
            if version:
                return query.filter(WorkflowModel.version == version).first()
            return query.order_by(desc(WorkflowModel.version)).first()

    @staticmethod
    def _to_info(model: WorkflowModel) -> WorkflowInfo:
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
            namespace=model.namespace,
            name=model.name,
            imports=model.imports,
            source=model.source,
            version=model.version,
            requests=requests,
            metadata=model.wf_metadata,
        )
