from __future__ import annotations

import pytest

from flux.catalogs import resolve_workflow_ref


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
