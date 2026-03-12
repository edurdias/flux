from __future__ import annotations

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Static
from textual.reactive import reactive


class StatCard(Widget):
    """A bordered stat card displaying a label, large value, and sublabel."""

    DEFAULT_CSS = """
    StatCard {
        border: solid #30363d;
        padding: 0 1;
        width: 1fr;
        height: 5;
        layout: vertical;
        content-align: center middle;
    }
    StatCard .stat-label {
        color: #8b949e;
        text-style: none;
    }
    StatCard .stat-value {
        text-style: bold;
    }
    StatCard .stat-sublabel {
        color: #484f58;
    }
    StatCard .stat-value.success { color: #3fb950; }
    StatCard .stat-value.warning { color: #d29922; }
    StatCard .stat-value.error { color: #f85149; }
    StatCard .stat-value.info { color: #58a6ff; }
    StatCard .stat-value.purple { color: #bc8cff; }
    """

    value = reactive("0")

    def __init__(
        self,
        label: str,
        value: str,
        sublabel: str,
        color: str = "",
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.label = label
        self.value = value
        self.sublabel = sublabel
        self.color = color

    def compose(self) -> ComposeResult:
        yield Static(self.label, classes="stat-label")
        yield Static(self.value, classes=f"stat-value {self.color}", id="stat-value")
        yield Static(self.sublabel, classes="stat-sublabel")

    def update_value(self, new_value: str) -> None:
        self.value = new_value
        try:
            value_widget = self.query_one("#stat-value", Static)
            value_widget.update(new_value)
        except Exception:
            pass
