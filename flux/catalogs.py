from __future__ import annotations

import ast
from abc import ABC
from abc import abstractmethod
from typing import Any


from sqlalchemy import and_
from sqlalchemy import desc
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from flux._namespace import DEFAULT_NAMESPACE
from flux._namespace import validate_namespace
from flux.errors import WorkflowNotFoundError
from flux.models import RepositoryFactory
from flux.models import WorkflowModel
from flux.domain.resource_request import ResourceRequest
from flux.utils import get_logger


def extract_workflow_input_schema(source: bytes, workflow_name: str) -> dict | None:
    """Extract JSON Schema from workflow's ExecutionContext[T] if T is a Pydantic BaseModel.

    Loads the source as a module, inspects type hints, and returns
    T.model_json_schema() if T is a BaseModel subclass. Returns None otherwise.
    """
    # NOTE: This function executes workflow source code to inspect type hints.
    # This runs in the same trust boundary as workflow registration — the server
    # already loads and executes workflow source for the catalog. The extraction
    # happens once at registration time, not on every request.
    import importlib.util
    import os
    import sys
    import tempfile
    import typing

    mod_name = f"_schema_extract_{workflow_name}"
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".py", delete=False, mode="wb") as f:
            tmp_path = f.name
            f.write(source)
            f.flush()
            spec = importlib.util.spec_from_file_location(mod_name, f.name)
            if not spec or not spec.loader:
                return None
            mod = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = mod
            spec.loader.exec_module(mod)

        func = getattr(mod, workflow_name, None)
        if func is None:
            return None
        if hasattr(func, "func"):
            func = func.func

        hints = typing.get_type_hints(func)
        ctx_hint = hints.get("ctx") or (hints[next(iter(hints))] if hints else None)
        if ctx_hint is None:
            return None

        args = typing.get_args(ctx_hint)
        if not args:
            return None

        input_type = args[0]

        try:
            from pydantic import BaseModel

            if isinstance(input_type, type) and issubclass(input_type, BaseModel):
                return input_type.model_json_schema()  # type: ignore[attr-defined]
        except ImportError:
            pass

        return None
    except Exception:
        return None
    finally:
        sys.modules.pop(mod_name, None)
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def extract_workflow_description(source: bytes, workflow_name: str) -> str | None:
    """Extract the docstring from a workflow function via AST."""
    try:
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name == workflow_name:
                    docstring = ast.get_docstring(node)
                    return docstring.strip() if docstring else None
        return None
    except Exception:
        return None


def _enrich_workflow_metadata(source: bytes, workflow_infos: list) -> None:
    """Extract input schemas and descriptions for all workflows from source."""
    for wf in workflow_infos:
        desc = extract_workflow_description(source, wf.name)
        if desc is not None:
            wf.metadata["description"] = desc

    import importlib.util
    import os
    import sys
    import tempfile
    import typing

    mod_name = "_schema_extract_batch"
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".py", delete=False, mode="wb") as f:
            tmp_path = f.name
            f.write(source)
            f.flush()
            spec = importlib.util.spec_from_file_location(mod_name, f.name)
            if not spec or not spec.loader:
                return
            mod = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = mod
            spec.loader.exec_module(mod)

        for wf in workflow_infos:
            func = getattr(mod, wf.name, None)
            if func is None:
                continue
            if hasattr(func, "func"):
                func = func.func
            try:
                hints = typing.get_type_hints(func)
            except Exception:
                continue
            ctx_hint = hints.get("ctx") or (hints[next(iter(hints))] if hints else None)
            if ctx_hint is None:
                continue
            args = typing.get_args(ctx_hint)
            if not args:
                continue
            input_type = args[0]
            try:
                from pydantic import BaseModel

                if isinstance(input_type, type) and issubclass(input_type, BaseModel):
                    wf.metadata["input_schema"] = input_type.model_json_schema()  # type: ignore[attr-defined]
            except ImportError:
                pass
    except Exception:
        pass
    finally:
        sys.modules.pop(mod_name, None)
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


logger = get_logger(__name__)


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
        return (validate_namespace(namespace, allow_reserved=True), name)
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
        affinity: dict[str, str] | list[dict] | None = None,
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
        self.affinity = affinity
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
            "affinity": self.affinity,
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
            SyntaxError: If the source code has invalid Python syntax, contains
                an invalid namespace declaration, or does not define any workflows.
                Line/column information from ``ast.parse`` is preserved; intentionally
                raised errors (invalid namespace, no workflow found) carry their own
                messages unchanged.
        """
        workflow_infos = self._parse_ast(source)
        _enrich_workflow_metadata(source, workflow_infos)
        return workflow_infos

    def parse_static(self, source: bytes) -> list[WorkflowInfo]:
        """Parse workflows from source WITHOUT executing the module.

        Performs only static AST analysis (no ``exec``/``import``), so it is
        safe to call on an untrusted upload *before* authorizing it. Metadata
        that requires importing the module (e.g. the input JSON schema) is left
        unpopulated until :meth:`enrich` runs.
        """
        return self._parse_ast(source)

    def enrich(self, source: bytes, workflow_infos: list[WorkflowInfo]) -> None:
        """Populate import-dependent metadata by executing the workflow source.

        Importing the uploaded module runs its top-level code, so callers MUST
        authorize the registration before invoking this.
        """
        _enrich_workflow_metadata(source, workflow_infos)

    def _parse_ast(self, source: bytes) -> list[WorkflowInfo]:
        """Static AST extraction shared by :meth:`parse` and :meth:`parse_static`."""
        try:
            tree = ast.parse(source)
        except SyntaxError:
            # Re-raise the original SyntaxError so line/column information is preserved
            raise

        try:
            # Results container
            workflow_infos = []

            # First pass: collect all imports regardless of their position in the
            # source. Collecting during the same walk as workflow extraction would
            # miss imports that appear after a workflow definition.
            imports: list[str] = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports.extend(name.name for name in node.names)
                elif isinstance(node, ast.ImportFrom):
                    module_prefix = f"{node.module}." if node.module else ""
                    imports.extend(f"{module_prefix}{name.name}" for name in node.names)

            # Second pass: extract workflow functions. Each WorkflowInfo gets its
            # own copy of the imports list so mutations to one don't leak to another.
            for node in ast.walk(tree):
                if isinstance(node, ast.AsyncFunctionDef):
                    workflow_name = None
                    workflow_namespace = DEFAULT_NAMESPACE
                    workflow_requests = None
                    workflow_affinity = None
                    workflow_durability = None
                    workflow_runner = None
                    workflow_routing = None

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
                            for kw in decorator.keywords:
                                if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                                    workflow_name = kw.value.value
                                elif kw.arg == "namespace" and isinstance(kw.value, ast.Constant):
                                    try:
                                        workflow_namespace = validate_namespace(kw.value.value)
                                    except ValueError as e:
                                        raise SyntaxError(
                                            f"Invalid namespace in @workflow.with_options: {e}",
                                        ) from e
                                elif kw.arg == "requests":
                                    workflow_requests = self._extract_workflow_requests(kw.value)
                                elif kw.arg == "affinity":
                                    workflow_affinity = self._extract_affinity(kw.value)
                                elif kw.arg == "durability" and isinstance(
                                    kw.value,
                                    ast.Constant,
                                ):
                                    workflow_durability = kw.value.value
                                elif kw.arg == "runner" and isinstance(
                                    kw.value,
                                    ast.Constant,
                                ):
                                    workflow_runner = kw.value.value
                                elif kw.arg == "routing":
                                    workflow_routing = self._extract_routing(kw.value)

                            if not workflow_name:
                                workflow_name = node.name

                            break

                    if workflow_name:
                        wf_metadata = self._extract_workflow_metadata(
                            node,
                            tree,
                        )
                        if workflow_durability is not None:
                            wf_metadata = dict(wf_metadata or {})
                            wf_metadata["durability"] = workflow_durability
                        if workflow_runner is not None:
                            wf_metadata = dict(wf_metadata or {})
                            wf_metadata["runner"] = workflow_runner
                        if workflow_routing is not None:
                            wf_metadata = dict(wf_metadata or {})
                            wf_metadata["routing"] = workflow_routing
                        workflow_infos.append(
                            WorkflowInfo(
                                id=f"{workflow_namespace}/{workflow_name}",
                                namespace=workflow_namespace,
                                name=workflow_name,
                                imports=list(imports),
                                source=source,
                                requests=workflow_requests,
                                affinity=workflow_affinity,
                                metadata=wf_metadata,
                            ),
                        )

            if not workflow_infos:
                raise SyntaxError("No workflow found in the provided code.")

            return workflow_infos

        except SyntaxError:
            # Intentionally raised above (e.g. invalid namespace, no workflows) —
            # re-raise unchanged so callers see the specific message.
            raise
        except Exception as e:
            raise SyntaxError(f"Error parsing source code: {e!s}") from e

    def _extract_workflow_metadata(
        self,
        func_node: ast.AsyncFunctionDef,
        tree: ast.Module,
    ) -> dict:
        task_func_to_name: dict[str, str] = {}
        exempt_func_to_name: dict[str, str] = {}
        workflow_func_to_ref: dict[str, tuple[str, str]] = {}
        secret_requests: set[str] = set()

        const_strings = self._collect_module_string_constants(tree)

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for dec in node.decorator_list:
                    if isinstance(dec, ast.Name) and dec.id == "task":
                        task_func_to_name[node.name] = node.name
                    elif isinstance(dec, ast.Name) and dec.id == "workflow":
                        workflow_func_to_ref[node.name] = (DEFAULT_NAMESPACE, node.name)
                    elif isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute):
                        if isinstance(dec.func.value, ast.Name):
                            if dec.func.value.id == "task" and dec.func.attr == "with_options":
                                task_name = self._extract_task_name_from_decorator(dec) or node.name
                                if self._is_auth_exempt(dec):
                                    exempt_func_to_name[node.name] = task_name
                                else:
                                    task_func_to_name[node.name] = task_name
                                secret_requests.update(
                                    self._extract_secret_requests_from_decorator(
                                        dec,
                                        const_strings,
                                    ),
                                )
                            elif (
                                dec.func.value.id == "workflow" and dec.func.attr == "with_options"
                            ):
                                wf_name = self._extract_task_name_from_decorator(dec) or node.name
                                wf_namespace = (
                                    self._extract_namespace_from_decorator(dec) or DEFAULT_NAMESPACE
                                )
                                workflow_func_to_ref[node.name] = (wf_namespace, wf_name)
                                secret_requests.update(
                                    self._extract_secret_requests_from_decorator(
                                        dec,
                                        const_strings,
                                    ),
                                )

        called_tasks = set()
        called_exempt = set()
        called_workflows: set[tuple[str, str]] = set()

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
                    elif func_name in workflow_func_to_ref:
                        called_workflows.add(workflow_func_to_ref[func_name])

        return {
            "task_names": sorted(called_tasks),
            "nested_workflows": sorted([list(ref) for ref in called_workflows]),
            "auth_exempt_tasks": sorted(called_exempt),
            "secret_requests": sorted(secret_requests),
        }

    @staticmethod
    def _collect_module_string_constants(tree: ast.Module) -> dict[str, str]:
        const_strings: dict[str, str] = {}
        for stmt in tree.body:
            if isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Constant):
                if isinstance(stmt.value.value, str):
                    for target in stmt.targets:
                        if isinstance(target, ast.Name):
                            const_strings[target.id] = stmt.value.value
        return const_strings

    @staticmethod
    def _extract_secret_requests_from_decorator(
        decorator: ast.Call,
        const_strings: dict[str, str] | None = None,
    ) -> list[str]:
        const_strings = const_strings or {}
        for kw in decorator.keywords:
            if kw.arg == "secret_requests" and isinstance(kw.value, (ast.List, ast.Tuple)):
                names: list[str] = []
                for elt in kw.value.elts:
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                        names.append(elt.value)
                    elif isinstance(elt, ast.Name) and elt.id in const_strings:
                        names.append(const_strings[elt.id])
                return names
        return []

    @staticmethod
    def _extract_task_name_from_decorator(decorator: ast.Call) -> str | None:
        for kw in decorator.keywords:
            if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                return kw.value.value
        return None

    @staticmethod
    def _extract_namespace_from_decorator(decorator: ast.Call) -> str | None:
        for kw in decorator.keywords:
            if kw.arg == "namespace" and isinstance(kw.value, ast.Constant):
                value = kw.value.value
                return value or None
        return None

    @staticmethod
    def _is_auth_exempt(decorator: ast.Call) -> bool:
        for kw in decorator.keywords:
            if kw.arg == "auth_exempt" and isinstance(kw.value, ast.Constant):
                return bool(kw.value.value)
        return False

    def _extract_affinity(self, node: ast.AST) -> dict[str, str] | list | None:
        if isinstance(node, ast.Dict):
            result = {}
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and isinstance(value, ast.Constant):
                    result[str(key.value)] = str(value.value)
            return result if result else None
        if isinstance(node, ast.Call) and (
            (isinstance(node.func, ast.Name) and node.func.id == "require")
            or (isinstance(node.func, ast.Attribute) and node.func.attr == "require")
        ):
            return self._extract_require(node)
        return None

    def _extract_require(self, node: ast.Call) -> list[dict]:
        """Extract an ``affinity=require(...)`` expression into its term specs.

        Like ``routing``, an unparseable expression raises instead of
        returning None: silently dropping a hard constraint would dispatch
        the workflow somewhere the author excluded. Building through the real
        ``flux.routing`` factories reuses their validation.
        """
        from typing import NoReturn

        from flux import routing as routing_dsl

        def fail(reason: str) -> NoReturn:
            raise SyntaxError(
                f"affinity expression must be statically declarable ({reason}); build "
                "it with flux.routing.require(...) using literal values or input(...)",
            )

        def call_name(call: ast.AST) -> str | None:
            if not isinstance(call, ast.Call):
                return None
            if isinstance(call.func, ast.Name):
                return call.func.id
            if isinstance(call.func, ast.Attribute):
                return call.func.attr
            return None

        def extract_input_ref(ref_node: ast.AST) -> Any:
            assert isinstance(ref_node, ast.Call)
            if len(ref_node.args) == 1 and isinstance(ref_node.args[0], ast.Constant):
                try:
                    return routing_dsl.input(ref_node.args[0].value)
                except (TypeError, ValueError) as e:
                    raise SyntaxError(f"Invalid input() reference: {e}") from e
            fail("input() takes a single literal path")

        def extract_selector(sel_node: ast.AST) -> Any:
            name = call_name(sel_node)
            assert isinstance(sel_node, ast.Call)
            if name in ("label", "meta"):
                factory = routing_dsl.label if name == "label" else routing_dsl.meta
                if len(sel_node.args) == 1 and isinstance(sel_node.args[0], ast.Constant):
                    try:
                        return factory(sel_node.args[0].value)
                    except (TypeError, ValueError) as e:
                        raise SyntaxError(f"Invalid affinity selector '{name}': {e}") from e
                fail(f"{name}() takes a literal key")
            if name == "label_for":
                if (
                    len(sel_node.args) == 2
                    and isinstance(sel_node.args[0], ast.Constant)
                    and call_name(sel_node.args[1]) == "input"
                ):
                    try:
                        return routing_dsl.label_for(
                            sel_node.args[0].value,
                            extract_input_ref(sel_node.args[1]),
                        )
                    except (TypeError, ValueError) as e:
                        raise SyntaxError(f"Invalid affinity selector 'label_for': {e}") from e
                fail("label_for() takes a literal prefix and input(...)")
            fail(f"expected label()/label_for()/meta(), got '{name}'")

        def extract_value(value_node: ast.AST) -> Any:
            if isinstance(value_node, ast.Constant):
                return value_node.value
            if call_name(value_node) == "input":
                return extract_input_ref(value_node)
            fail(f"unsupported value expression at line {getattr(value_node, 'lineno', '?')}")

        _AST_OPS = {ast.Eq: "==", ast.NotEq: "!="}

        def extract_condition(cond_node: ast.AST) -> Any:
            """A label comparison: label(...)/label_for(...) vs constant/input."""
            if not isinstance(cond_node, ast.Compare) or len(cond_node.ops) != 1:
                fail(
                    "require() terms are single label comparisons, "
                    'e.g. label("region") == input("region")',
                )
            op = _AST_OPS.get(type(cond_node.ops[0]))
            if op is None:
                fail(
                    f"require() supports only == and != (line {cond_node.lineno}); "
                    "ordered comparisons belong to routing=",
                )
            left, right = cond_node.left, cond_node.comparators[0]
            if call_name(left) in ("label", "label_for", "meta"):
                selector, value = extract_selector(left), extract_value(right)
            elif call_name(right) in ("label", "label_for", "meta"):
                # == and != are symmetric, so no operator flip is needed.
                selector, value = extract_selector(right), extract_value(left)
            else:
                fail("one side of a require() comparison must be label()/label_for()/meta()")
            try:
                return routing_dsl.Condition(selector, op, value)
            except ValueError as e:
                raise SyntaxError(f"Invalid affinity condition: {e}") from e

        def extract_input_condition(cond_node: ast.AST) -> Any:
            """A when() condition: input(...) vs constant, either order."""
            if not isinstance(cond_node, ast.Compare) or len(cond_node.ops) != 1:
                fail('when() takes an input comparison, e.g. input("tier") == "dedicated"')
            op = _AST_OPS.get(type(cond_node.ops[0]))
            if op is None:
                fail(f"when() conditions support only == and != (line {cond_node.lineno})")
            left, right = cond_node.left, cond_node.comparators[0]
            if call_name(left) == "input":
                ref, const = extract_input_ref(left), right
            elif call_name(right) == "input":
                ref, const = extract_input_ref(right), left
            else:
                fail("one side of a when() condition must be input(...)")
            if not isinstance(const, ast.Constant):
                fail("when() conditions compare input(...) against a literal")
            try:
                return routing_dsl.InputCondition(ref, op, const.value)
            except ValueError as e:
                raise SyntaxError(f"Invalid affinity when() condition: {e}") from e

        def extract_service(svc_node: ast.AST) -> Any:
            assert isinstance(svc_node, ast.Call)
            if len(svc_node.args) != 1:
                fail("service() takes exactly one argument")
            arg = svc_node.args[0]
            if isinstance(arg, ast.Constant):
                value: Any = arg.value
            elif call_name(arg) == "input":
                value = extract_input_ref(arg)
            else:
                fail("service() takes a literal name or input(...)")
            try:
                return routing_dsl.service(value)
            except (TypeError, ValueError) as e:
                raise SyntaxError(f"Invalid affinity term 'service': {e}") from e

        def extract_match_term(term_node: ast.AST) -> Any:
            if call_name(term_node) == "service":
                return extract_service(term_node)
            return extract_condition(term_node)

        terms = []
        for term_node in node.args:
            name = call_name(term_node)
            try:
                if name == "optional":
                    assert isinstance(term_node, ast.Call)
                    if len(term_node.args) != 1:
                        fail("optional() takes exactly one term")
                    terms.append(routing_dsl.optional(extract_match_term(term_node.args[0])))
                elif name == "when":
                    assert isinstance(term_node, ast.Call)
                    if len(term_node.args) != 2:
                        fail("when() takes a condition and a term")
                    terms.append(
                        routing_dsl.when(
                            extract_input_condition(term_node.args[0]),
                            extract_match_term(term_node.args[1]),
                        ),
                    )
                else:
                    terms.append(extract_match_term(term_node))
            except (TypeError, ValueError) as e:
                raise SyntaxError(f"Invalid affinity term '{name}': {e}") from e
        for kw in node.keywords:
            fail(f"require() takes no keyword arguments, got '{kw.arg}'")
        try:
            return routing_dsl.require(*terms)
        except ValueError as e:
            raise SyntaxError(f"Invalid affinity expression: {e}") from e

    def _extract_routing(self, node: ast.AST) -> dict | None:
        """Extract a ``routing=score(...)`` policy into its JSON spec.

        Unlike ``requests``/``affinity``, an unparseable routing policy raises
        instead of returning None: silently dropping it would dispatch the
        workflow with different semantics than the author declared. Building
        through the real ``flux.routing`` factories reuses their validation.
        """
        from typing import NoReturn

        from flux import routing as routing_dsl

        def fail(reason: str) -> NoReturn:
            raise SyntaxError(
                f"routing policy must be statically declarable ({reason}); build it "
                "with flux.routing.score(...) using literal values or input(...)",
            )

        def call_name(call: ast.AST) -> str | None:
            if not isinstance(call, ast.Call):
                return None
            if isinstance(call.func, ast.Name):
                return call.func.id
            if isinstance(call.func, ast.Attribute):
                return call.func.attr
            return None

        from collections.abc import Callable

        _SELECTOR_FACTORIES: dict[str, Callable[..., Any]] = {
            "label": routing_dsl.label,
            "metric": routing_dsl.metric,
            "meta": routing_dsl.meta,
            "resource": routing_dsl.resource,
            "load": routing_dsl.load,
        }

        def extract_input_ref(ref_node: ast.AST) -> Any:
            assert isinstance(ref_node, ast.Call)
            if len(ref_node.args) == 1 and isinstance(ref_node.args[0], ast.Constant):
                try:
                    return routing_dsl.input(ref_node.args[0].value)
                except (TypeError, ValueError) as e:
                    raise SyntaxError(f"Invalid input() reference: {e}") from e
            fail("input() takes a single literal path")

        def extract_selector(sel_node: ast.AST) -> Any:
            name = call_name(sel_node)
            if name is None:
                fail(
                    f"expected label()/label_for()/metric()/meta()/resource()/load(), got '{name}'",
                )
            assert isinstance(sel_node, ast.Call)
            if name == "label_for":
                if (
                    len(sel_node.args) == 2
                    and isinstance(sel_node.args[0], ast.Constant)
                    and call_name(sel_node.args[1]) == "input"
                ):
                    try:
                        return routing_dsl.label_for(
                            sel_node.args[0].value,
                            extract_input_ref(sel_node.args[1]),
                        )
                    except (TypeError, ValueError) as e:
                        raise SyntaxError(f"Invalid routing selector 'label_for': {e}") from e
                fail("label_for() takes a literal prefix and input(...)")
            factory = _SELECTOR_FACTORIES.get(name or "")
            if factory is None:
                fail(
                    f"expected label()/label_for()/metric()/meta()/resource()/load(), got '{name}'",
                )
            args = []
            for arg in sel_node.args:
                if not isinstance(arg, ast.Constant):
                    fail(f"{name}() takes a literal key")
                args.append(arg.value)
            try:
                return factory(*args)
            except (TypeError, ValueError) as e:
                raise SyntaxError(f"Invalid routing selector '{name}': {e}") from e

        def extract_value(value_node: ast.AST) -> Any:
            if isinstance(value_node, ast.Constant):
                return value_node.value
            if call_name(value_node) == "input":
                return extract_input_ref(value_node)
            fail(f"unsupported value expression at line {getattr(value_node, 'lineno', '?')}")

        _AST_OPS = {
            ast.Eq: "==",
            ast.NotEq: "!=",
            ast.Lt: "<",
            ast.LtE: "<=",
            ast.Gt: ">",
            ast.GtE: ">=",
        }
        # For "value <op> selector" order: same condition, operator flipped.
        _FLIPPED = {"==": "==", "!=": "!=", "<": ">", "<=": ">=", ">": "<", ">=": "<="}

        def extract_condition(cond_node: ast.AST) -> Any:
            if not isinstance(cond_node, ast.Compare) or len(cond_node.ops) != 1:
                fail(
                    "prefer() takes a single selector comparison, "
                    'e.g. prefer(label("region") == "eu-west")',
                )
            op = _AST_OPS.get(type(cond_node.ops[0]))
            if op is None:
                fail(f"unsupported comparison operator at line {cond_node.lineno}")
            left, right = cond_node.left, cond_node.comparators[0]
            selector_names = (*_SELECTOR_FACTORIES, "label_for")
            if call_name(left) in selector_names:
                selector, value = extract_selector(left), extract_value(right)
            elif call_name(right) in selector_names:
                selector, value, op = extract_selector(right), extract_value(left), _FLIPPED[op]
            else:
                fail("one side of a prefer() comparison must be a selector")
            try:
                return routing_dsl.Condition(selector, op, value)
            except ValueError as e:
                raise SyntaxError(f"Invalid routing condition: {e}") from e

        def extract_input_condition(cond_node: ast.AST) -> Any:
            """A when() condition: input(...) vs constant, either order."""
            if not isinstance(cond_node, ast.Compare) or len(cond_node.ops) != 1:
                fail('when() takes an input comparison, e.g. input("tier") == "dedicated"')
            op = _AST_OPS.get(type(cond_node.ops[0]))
            if op not in ("==", "!="):
                fail(f"when() conditions support only == and != (line {cond_node.lineno})")
            left, right = cond_node.left, cond_node.comparators[0]
            if call_name(left) == "input":
                ref, const = extract_input_ref(left), right
            elif call_name(right) == "input":
                ref, const = extract_input_ref(right), left
            else:
                fail("one side of a when() condition must be input(...)")
            if not isinstance(const, ast.Constant):
                fail("when() conditions compare input(...) against a literal")
            try:
                return routing_dsl.InputCondition(ref, op, const.value)
            except ValueError as e:
                raise SyntaxError(f"Invalid routing when() condition: {e}") from e

        def extract_service(svc_node: ast.AST) -> Any:
            assert isinstance(svc_node, ast.Call)
            if len(svc_node.args) != 1:
                fail("service() takes exactly one argument")
            arg = svc_node.args[0]
            if isinstance(arg, ast.Constant):
                value: Any = arg.value
            elif call_name(arg) == "input":
                value = extract_input_ref(arg)
            else:
                fail("service() takes a literal name or input(...)")
            try:
                return routing_dsl.service(value)
            except (TypeError, ValueError) as e:
                raise SyntaxError(f"Invalid routing term 'service': {e}") from e

        def extract_term(term_node: ast.AST) -> Any:
            name = call_name(term_node)
            if name not in ("prefer", "least", "most", "sticky", "when"):
                fail(f"expected prefer()/least()/most()/sticky()/when() terms, got '{name}'")
            assert isinstance(term_node, ast.Call)
            kwargs = {}
            for kw in term_node.keywords:
                if kw.arg is None:
                    fail("**kwargs is not supported in routing terms")
                kwargs[kw.arg] = extract_value(kw.value)
            try:
                if name == "sticky":
                    if term_node.args:
                        fail("sticky() takes no positional arguments")
                    return routing_dsl.sticky(**kwargs)
                if name == "when":
                    if len(term_node.args) != 2 or kwargs:
                        fail("when() takes a condition and a term, and no keyword arguments")
                    return routing_dsl.when(
                        extract_input_condition(term_node.args[0]),
                        extract_term(term_node.args[1]),
                    )
                if len(term_node.args) != 1:
                    fail(f"{name}() takes exactly one positional argument")
                if name == "prefer":
                    arg = term_node.args[0]
                    if call_name(arg) == "service":
                        return routing_dsl.prefer(extract_service(arg), **kwargs)
                    return routing_dsl.prefer(extract_condition(arg), **kwargs)
                if name == "least":
                    return routing_dsl.least(extract_selector(term_node.args[0]), **kwargs)
                return routing_dsl.most(extract_selector(term_node.args[0]), **kwargs)
            except (TypeError, ValueError) as e:
                raise SyntaxError(f"Invalid routing term '{name}': {e}") from e

        if call_name(node) != "score":
            fail("expected a score(...) call")
        assert isinstance(node, ast.Call)

        terms = [extract_term(term_node) for term_node in node.args]
        try:
            return routing_dsl.score(*terms)
        except ValueError as e:
            raise SyntaxError(f"Invalid routing policy: {e}") from e

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
    def create() -> DatabaseWorkflowCatalog:
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
                        requests=requests_dict or None,
                        affinity=wf.affinity or None,
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
            affinity=model.affinity,
            metadata=model.wf_metadata,
        )
