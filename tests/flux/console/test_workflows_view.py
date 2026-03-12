from __future__ import annotations

from flux.console.screens.workflows import WorkflowsView


class TestWorkflowsView:
    def test_creates(self):
        view = WorkflowsView()
        assert view is not None
