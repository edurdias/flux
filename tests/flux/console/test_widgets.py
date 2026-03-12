from __future__ import annotations

import json

import pytest

from flux.console.widgets.gantt_chart import GanttChart
from flux.console.widgets.json_viewer import format_json, truncate_json
from flux.console.widgets.resource_bar import ResourceBar
from flux.console.widgets.run_history import RunHistoryChart
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


class TestResourceBar:
    def test_creates_with_values(self):
        bar = ResourceBar(label="CPU", used=4.5, total=8.0, unit="")
        assert bar.label == "CPU"
        assert bar.used == 4.5
        assert bar.total == 8.0

    def test_percentage(self):
        bar = ResourceBar(label="MEM", used=10.0, total=16.0, unit="G")
        assert bar.percentage == pytest.approx(62.5)

    def test_color_green_under_60(self):
        bar = ResourceBar(label="CPU", used=4.0, total=8.0, unit="")
        assert bar.bar_color == "success"

    def test_color_yellow_60_to_85(self):
        bar = ResourceBar(label="CPU", used=6.0, total=8.0, unit="")
        assert bar.bar_color == "warning"

    def test_color_red_above_85(self):
        bar = ResourceBar(label="CPU", used=7.5, total=8.0, unit="")
        assert bar.bar_color == "error"

    def test_zero_total_no_crash(self):
        bar = ResourceBar(label="CPU", used=0.0, total=0.0, unit="")
        assert bar.percentage == 0.0


class TestJsonViewer:
    def test_truncate_short_value(self):
        result = truncate_json({"key": "val"})
        assert result == '{"key": "val"}'

    def test_truncate_long_value(self):
        data = {"key": "a" * 200}
        result = truncate_json(data, max_length=80)
        assert len(result) <= 83  # 80 + "..."
        assert result.endswith("...")

    def test_format_json_pretty(self):
        data = {"key": "val", "num": 42}
        result = format_json(data)
        assert "  " in result  # indented
        parsed = json.loads(result)
        assert parsed == data

    def test_format_non_dict(self):
        result = format_json("just a string")
        assert result == '"just a string"'

    def test_format_none(self):
        result = format_json(None)
        assert result == "null"


class TestGanttChart:
    def test_creates_with_events(self):
        events = [
            {"type": "WORKFLOW_STARTED", "name": "wf", "time": "2026-03-12T14:23:01.000"},
            {"type": "TASK_STARTED", "name": "task_a", "time": "2026-03-12T14:23:01.100"},
            {"type": "TASK_COMPLETED", "name": "task_a", "time": "2026-03-12T14:23:01.500"},
            {"type": "WORKFLOW_COMPLETED", "name": "wf", "time": "2026-03-12T14:23:02.000"},
        ]
        chart = GanttChart(events)
        assert chart is not None


class TestRunHistoryChart:
    def test_creates_with_executions(self):
        executions = [
            {
                "state": "COMPLETED",
                "events": [
                    {"type": "WORKFLOW_STARTED", "time": "2026-03-12T14:23:01.000"},
                    {"type": "WORKFLOW_COMPLETED", "time": "2026-03-12T14:23:03.312"},
                ],
            },
        ]
        chart = RunHistoryChart(executions)
        assert chart is not None

    def test_creates_empty(self):
        chart = RunHistoryChart([])
        assert chart is not None
