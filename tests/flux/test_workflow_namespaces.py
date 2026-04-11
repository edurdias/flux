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
