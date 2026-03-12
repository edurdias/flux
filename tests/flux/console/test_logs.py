from __future__ import annotations

from flux.console.screens.logs import LogsView


class TestLogsView:
    def test_creates(self):
        view = LogsView()
        assert view is not None
