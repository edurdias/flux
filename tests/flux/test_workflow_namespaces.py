from __future__ import annotations

import pytest

from flux.catalogs import resolve_workflow_ref
from flux.workflow import workflow


class TestResolveWorkflowRef:
    def test_qualified_reference(self):
        assert resolve_workflow_ref("billing/invoice") == ("billing", "invoice")

    def test_bare_name_resolves_to_default(self):
        assert resolve_workflow_ref("hello_world") == ("default", "hello_world")

    def test_multi_slash_rejected(self):
        with pytest.raises(ValueError, match="flat namespaces"):
            resolve_workflow_ref("a/b/c")

    def test_empty_string_rejected(self):
        with pytest.raises(ValueError, match="empty"):
            resolve_workflow_ref("")

    def test_none_rejected(self):
        with pytest.raises(ValueError, match="empty"):
            resolve_workflow_ref(None)  # type: ignore[arg-type]

    def test_empty_namespace_segment_rejected(self):
        with pytest.raises(ValueError, match="empty"):
            resolve_workflow_ref("/invoice")

    def test_empty_name_segment_rejected(self):
        with pytest.raises(ValueError, match="empty"):
            resolve_workflow_ref("billing/")


class TestWorkflowNamespace:
    def test_default_namespace(self):
        @workflow
        async def hello(ctx):
            return None

        assert hello.namespace == "default"
        assert hello.qualified_name == "default/hello"

    def test_explicit_namespace(self):
        @workflow.with_options(namespace="billing")
        async def invoice(ctx):
            return None

        assert invoice.namespace == "billing"
        assert invoice.name == "invoice"
        assert invoice.qualified_name == "billing/invoice"

    def test_explicit_name_and_namespace(self):
        @workflow.with_options(name="invoice_v2", namespace="billing")
        async def invoice(ctx):
            return None

        assert invoice.name == "invoice_v2"
        assert invoice.namespace == "billing"
        assert invoice.qualified_name == "billing/invoice_v2"

    def test_none_namespace_resolves_to_default(self):
        @workflow.with_options(namespace=None)
        async def foo(ctx):
            return None

        assert foo.namespace == "default"

    def test_empty_string_namespace_resolves_to_default(self):
        @workflow.with_options(namespace="")
        async def foo(ctx):
            return None

        assert foo.namespace == "default"

    def test_invalid_namespace_rejected(self):
        with pytest.raises(ValueError, match="namespace"):

            @workflow.with_options(namespace="Billing")  # uppercase
            async def foo(ctx):
                return None

    def test_namespace_with_slash_rejected(self):
        with pytest.raises(ValueError, match="namespace"):

            @workflow.with_options(namespace="a/b")
            async def foo(ctx):
                return None

    def test_namespace_too_long_rejected(self):
        with pytest.raises(ValueError, match="namespace"):

            @workflow.with_options(namespace="a" * 65)
            async def foo(ctx):
                return None

    def test_namespace_leading_underscore_rejected(self):
        with pytest.raises(ValueError, match="namespace"):

            @workflow.with_options(namespace="_bad")
            async def foo(ctx):
                return None

    def test_explicit_default_namespace_accepted(self):
        @workflow.with_options(namespace="default")
        async def foo(ctx):
            return None

        assert foo.namespace == "default"


def test_inline_run_registers_namespaced_workflow(tmp_path, monkeypatch):
    """Task 6: _ensure_registered should register the workflow in the declared namespace.

    We don't actually run the workflow here — that requires Task 7 to add
    workflow_namespace to ExecutionContext. We only verify that _ensure_registered
    looks up and registers using the (namespace, name) pair.
    """
    monkeypatch.setenv("FLUX_DATABASE_URL", f"sqlite:///{tmp_path}/inline.db")
    from flux.config import Configuration

    Configuration._instance = None  # type: ignore[attr-defined]
    Configuration._config = None  # type: ignore[attr-defined]

    from flux.catalogs import DatabaseWorkflowCatalog

    source_path = tmp_path / "my_flow.py"
    source_path.write_text(
        """
from flux import workflow

@workflow.with_options(namespace="billing")
async def do_thing(ctx):
    return "done"
""".lstrip(),
    )

    import sys
    import importlib.util

    spec = importlib.util.spec_from_file_location("my_flow", source_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # inspect.getmodule() resolves via sys.modules; register before calling
    # _ensure_registered so it can find the source file.
    monkeypatch.setitem(sys.modules, "my_flow", module)
    spec.loader.exec_module(module)

    wf = module.do_thing

    # Call _ensure_registered directly to isolate Task 6 from Task 7 dependencies
    workflow_id = wf._ensure_registered()
    assert workflow_id is not None

    catalog = DatabaseWorkflowCatalog()
    registered = catalog.get("billing", "do_thing")
    assert registered.namespace == "billing"
    assert registered.name == "do_thing"
