from __future__ import annotations

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Static, ProgressBar


class ResourceBar(Widget):
    """Resource usage bar with label, progress, and used/total values."""

    DEFAULT_CSS = """
    ResourceBar {
        layout: horizontal;
        height: 1;
        width: 1fr;
    }
    ResourceBar .res-label {
        width: 5;
        color: #8b949e;
    }
    ResourceBar .res-bar {
        width: 1fr;
    }
    ResourceBar .res-value {
        width: 12;
        color: #8b949e;
        text-align: right;
    }
    """

    def __init__(
        self,
        label: str,
        used: float,
        total: float,
        unit: str = "",
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.label = label
        self.used = used
        self.total = total
        self.unit = unit

    @property
    def percentage(self) -> float:
        if self.total <= 0:
            return 0.0
        return (self.used / self.total) * 100

    @property
    def bar_color(self) -> str:
        pct = self.percentage
        if pct >= 85:
            return "error"
        elif pct >= 60:
            return "warning"
        return "success"

    def compose(self) -> ComposeResult:
        yield Static(self.label, classes="res-label")
        bar = ProgressBar(total=100, show_eta=False, show_percentage=False, classes="res-bar")
        bar.advance(self.percentage)
        yield bar
        if self.unit:
            yield Static(
                f"{self.used:.0f}/{self.total:.0f}{self.unit}",
                classes="res-value",
            )
        else:
            yield Static(
                f"{self.used:.1f}/{self.total:.0f}",
                classes="res-value",
            )

    def update_values(self, used: float, total: float) -> None:
        self.used = used
        self.total = total
