from __future__ import annotations

from flux.console.widgets.stat_card import StatCard


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
