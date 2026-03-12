from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import DataTable, Static

from flux.console.widgets.json_viewer import JsonViewerModal


class LogsView(Widget):
    """Logs view showing aggregated event stream."""

    DEFAULT_CSS = """
    LogsView {
        layout: vertical;
        height: 1fr;
    }
    LogsView #logs-header {
        height: 1;
        margin-bottom: 1;
    }
    LogsView #logs-table {
        height: 1fr;
    }
    """

    BINDINGS = [
        ("s", "toggle_autoscroll", "Auto-scroll"),
        ("v", "view_event", "View Event"),
    ]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._events: list[dict[str, Any]] = []
        self._autoscroll = True

    def compose(self) -> ComposeResult:
        yield Static(
            "[#8b949e]EVENT LOG[/]  [#3fb950]● Auto-scroll ON[/]  [#484f58]s=Toggle  v=View[/]",
            id="logs-header",
        )
        table = DataTable(id="logs-table")
        table.cursor_type = "row"
        yield table

    def on_mount(self) -> None:
        table = self.query_one("#logs-table", DataTable)
        table.add_columns("Time", "Type", "Workflow", "Name", "Detail")

    def update_data(self, executions: list[dict[str, Any]]) -> None:
        """Extract and display events from executions."""
        all_events = []
        for ex in executions:
            workflow_name = ex.get("workflow_name", "\u2014")
            for ev in ex.get("events", []):
                ev_copy = dict(ev)
                ev_copy["_workflow"] = workflow_name
                all_events.append(ev_copy)

        # Sort by time descending
        all_events.sort(
            key=lambda e: e.get("time") or e.get("timestamp", ""),
            reverse=True,
        )

        self._events = all_events[:200]  # Keep last 200

        table = self.query_one("#logs-table", DataTable)
        table.clear()

        for ev in self._events:
            etime = ev.get("time") or ev.get("timestamp", "\u2014")
            if isinstance(etime, str) and "T" in etime:
                etime = etime.split("T")[1][:12]

            etype = ev.get("type", "\u2014")
            workflow = ev.get("_workflow", "\u2014")
            name = ev.get("name", "\u2014")

            # Color by event type
            type_color = "#8b949e"
            if "COMPLETED" in etype:
                type_color = "#3fb950"
            elif "FAILED" in etype:
                type_color = "#f85149"
            elif "STARTED" in etype:
                type_color = "#d29922"
            elif "CANCELLED" in etype:
                type_color = "#484f58"

            # Extract brief detail
            detail = ""
            if ev.get("error"):
                detail = str(ev["error"])[:40]
            elif ev.get("value"):
                detail = str(ev["value"])[:40]

            table.add_row(etime, f"[{type_color}]{etype}[/]", workflow, name, detail)

        if self._autoscroll and self._events:
            try:
                table.move_cursor(row=0)
            except Exception:
                pass

    def action_toggle_autoscroll(self) -> None:
        self._autoscroll = not self._autoscroll
        status = (
            "[#3fb950]● Auto-scroll ON[/]" if self._autoscroll else "[#f85149]○ Auto-scroll OFF[/]"
        )
        try:
            self.query_one("#logs-header", Static).update(
                f"[#8b949e]EVENT LOG[/]  {status}  [#484f58]s=Toggle  v=View[/]",
            )
        except Exception:
            pass

    def action_view_event(self) -> None:
        table = self.query_one("#logs-table", DataTable)
        row_index = table.cursor_row
        if 0 <= row_index < len(self._events):
            event = self._events[row_index]
            self.app.push_screen(JsonViewerModal("Event Detail", event))
