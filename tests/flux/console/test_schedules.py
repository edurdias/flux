from __future__ import annotations

from flux.console.screens.schedules import SchedulesView


class TestSchedulesView:
    def test_creates(self):
        view = SchedulesView()
        assert view is not None
