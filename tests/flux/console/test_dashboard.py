from __future__ import annotations

from flux.console.screens.dashboard import DashboardView


class TestDashboardView:
    def test_creates(self):
        view = DashboardView()
        assert view is not None
