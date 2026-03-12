from __future__ import annotations

from flux.console.screens.executions import ExecutionsView


class TestExecutionsView:
    def test_creates(self):
        view = ExecutionsView()
        assert view is not None
