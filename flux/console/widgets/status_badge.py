from __future__ import annotations

from textual.widgets import Static


STATE_STYLES = {
    "CREATED": ("badge-scheduled", "CREATED"),
    "SCHEDULED": ("badge-scheduled", "SCHEDULED"),
    "CLAIMED": ("badge-scheduled", "CLAIMED"),
    "RUNNING": ("badge-running", "RUNNING"),
    "COMPLETED": ("badge-completed", "COMPLETED"),
    "FAILED": ("badge-failed", "FAILED"),
    "PAUSED": ("badge-paused", "PAUSED"),
    "RESUMING": ("badge-running", "RESUMING"),
    "CANCELLING": ("badge-running", "CANCELLING"),
    "CANCELLED": ("badge-cancelled", "CANCELLED"),
}


class StatusBadge(Static):
    """Colored badge showing execution state."""

    DEFAULT_CSS = """
    StatusBadge {
        width: auto;
        height: 1;
        padding: 0 1;
    }
    """

    def __init__(self, state: str, **kwargs):
        self.state = state
        css_class, display = STATE_STYLES.get(state, ("badge-cancelled", state))
        super().__init__(display, classes=css_class, **kwargs)

    def update_state(self, new_state: str) -> None:
        self.state = new_state
        css_class, display = STATE_STYLES.get(new_state, ("badge-cancelled", new_state))
        self.update(display)
        self.set_classes(css_class)
