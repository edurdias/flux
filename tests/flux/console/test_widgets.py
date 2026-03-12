from __future__ import annotations

from flux.console.widgets.stat_card import StatCard
from flux.console.widgets.status_badge import StatusBadge


class TestStatCard:
    def test_creates_with_required_params(self):
        card = StatCard(label="WORKFLOWS", value="12", sublabel="registered")
        assert card.label == "WORKFLOWS"
        assert card.value == "12"
        assert card.sublabel == "registered"

    def test_creates_with_color(self):
        card = StatCard(label="RUNNING", value="3", sublabel="executions", color="warning")
        assert card.color == "warning"

    def test_update_value(self):
        card = StatCard(label="TEST", value="0", sublabel="items")
        card.update_value("42")
        assert card.value == "42"


class TestStatusBadge:
    def test_maps_state_to_class(self):
        badge = StatusBadge("RUNNING")
        assert badge.state == "RUNNING"

    def test_known_states(self):
        for state in ["RUNNING", "COMPLETED", "FAILED", "PAUSED", "CANCELLED", "SCHEDULED"]:
            badge = StatusBadge(state)
            assert badge.state == state

    def test_update_state(self):
        badge = StatusBadge("RUNNING")
        badge.update_state("COMPLETED")
        assert badge.state == "COMPLETED"
