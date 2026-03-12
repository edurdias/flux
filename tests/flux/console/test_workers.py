from __future__ import annotations

from flux.console.screens.workers import WorkersView


class TestWorkersView:
    def test_creates(self):
        view = WorkersView()
        assert view is not None
