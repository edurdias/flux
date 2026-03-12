from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widget import Widget
from textual.widgets import DataTable, Static

from flux.console.widgets.gantt_chart import GanttChart
from flux.console.widgets.json_viewer import JsonViewerModal, truncate_json


class ExecutionsView(Widget):
    """Executions view with list and detail modes."""

    DEFAULT_CSS = """
    ExecutionsView {
        layout: vertical;
        height: 1fr;
    }
    ExecutionsView #exec-filter-bar {
        height: 1;
        margin-bottom: 1;
    }
    ExecutionsView #exec-table {
        height: 1fr;
    }
    ExecutionsView #exec-detail {
        display: none;
        height: 1fr;
        layout: vertical;
    }
    ExecutionsView #exec-detail.visible {
        display: block;
    }
    ExecutionsView #exec-info-bar {
        height: 3;
        border: solid #30363d;
        padding: 0 1;
        margin-bottom: 1;
    }
    ExecutionsView #exec-gantt-container {
        height: auto;
        min-height: 4;
        max-height: 12;
        border: solid #30363d;
        margin-bottom: 1;
        padding: 0 1;
    }
    ExecutionsView #exec-events-panel {
        layout: horizontal;
        height: 1fr;
    }
    ExecutionsView #event-list-panel {
        width: 1fr;
        border: solid #30363d;
        margin-right: 1;
    }
    ExecutionsView #event-detail-panel {
        width: 1fr;
        border: solid #30363d;
        padding: 1;
    }
    ExecutionsView .section-title {
        color: #8b949e;
        text-style: bold;
        height: 1;
        padding: 0 1;
    }
    """

    BINDINGS = [
        ("escape", "back_to_list", "Back"),
        ("v", "view_data", "View Data"),
    ]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._executions: list[dict[str, Any]] = []
        self._current_execution: dict[str, Any] | None = None
        self._detail_visible = False
        self._selected_event_index = 0

    def compose(self) -> ComposeResult:
        # List mode
        yield Static(
            "[#8b949e]EXECUTIONS[/]  [#484f58]Enter=Detail  Esc=Back[/]",
            id="exec-filter-bar",
        )
        table = DataTable(id="exec-table")
        table.cursor_type = "row"
        yield table

        # Detail mode (hidden initially)
        with Vertical(id="exec-detail"):
            yield Static("", id="exec-info-bar")
            yield Vertical(id="exec-gantt-container")
            with Horizontal(id="exec-events-panel"):
                with Vertical(id="event-list-panel"):
                    yield Static("EVENTS", classes="section-title")
                    event_table = DataTable(id="event-table")
                    event_table.cursor_type = "row"
                    yield event_table
                with Vertical(id="event-detail-panel"):
                    yield Static("EVENT DETAIL", classes="section-title")
                    yield Static("Select an event", id="event-detail-content")

    def on_mount(self) -> None:
        table = self.query_one("#exec-table", DataTable)
        table.add_columns("State", "Workflow", "Exec ID", "Duration", "Worker", "Started")

        event_table = self.query_one("#event-table", DataTable)
        event_table.add_columns("Type", "Name", "Time")

    def update_data(self, executions_data: dict[str, Any]) -> None:
        """Update execution list from API response."""
        if self._detail_visible:
            return  # Don't update list while viewing detail

        self._executions = executions_data.get("executions", [])
        table = self.query_one("#exec-table", DataTable)
        table.clear()

        for ex in self._executions:
            state = ex.get("state", "UNKNOWN")
            workflow = ex.get("workflow_name", "\u2014")
            exec_id = str(ex.get("execution_id", ""))[:12]
            duration = ex.get("duration", "\u2014")
            if isinstance(duration, (int, float)):
                duration = f"{duration:.1f}s"
            worker = ex.get("worker_name", "\u2014") or "\u2014"
            started = ex.get("started_at", "\u2014") or "\u2014"
            if isinstance(started, str) and "T" in started:
                started = started.split("T")[1][:8]

            state_icon = {
                "COMPLETED": "[#3fb950]\u2713[/]",
                "FAILED": "[#f85149]\u2717[/]",
                "RUNNING": "[#d29922]\u25b6[/]",
                "CANCELLED": "[#8b949e]\u25cb[/]",
                "PAUSED": "[#bc8cff]\u23f8[/]",
            }.get(state, "[#8b949e]\u25cb[/]")

            table.add_row(
                f"{state_icon} {state}",
                workflow,
                exec_id,
                str(duration),
                worker,
                started,
            )

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Handle row selection in either the exec table or event table."""
        table_id = event.data_table.id

        if table_id == "exec-table" and not self._detail_visible:
            row_index = event.cursor_row
            if 0 <= row_index < len(self._executions):
                self._show_detail(self._executions[row_index])
        elif table_id == "event-table":
            self._show_event_detail(event.cursor_row)

    def _show_detail(self, execution: dict[str, Any]) -> None:
        """Switch to detail mode for the selected execution."""
        self._current_execution = execution
        self._detail_visible = True

        # Hide list, show detail
        self.query_one("#exec-table", DataTable).display = False
        detail = self.query_one("#exec-detail", Vertical)
        detail.display = True

        # Update info bar
        state = execution.get("state", "UNKNOWN")
        workflow = execution.get("workflow_name", "\u2014")
        exec_id = str(execution.get("execution_id", ""))
        worker = execution.get("worker_name", "\u2014") or "\u2014"
        duration = execution.get("duration", "\u2014")
        if isinstance(duration, (int, float)):
            duration = f"{duration:.1f}s"

        state_color = {
            "COMPLETED": "#3fb950",
            "FAILED": "#f85149",
            "RUNNING": "#d29922",
        }.get(state, "#8b949e")

        info_text = (
            f"[{state_color}]{state}[/]  [bold]{workflow}[/]  "
            f"[#484f58]ID:[/] {exec_id[:20]}  "
            f"[#484f58]Worker:[/] {worker}  "
            f"[#484f58]Duration:[/] {duration}"
        )
        try:
            self.query_one("#exec-info-bar", Static).update(info_text)
        except Exception:
            pass

        # Update Gantt chart
        events = execution.get("events", [])
        try:
            gantt_container = self.query_one("#exec-gantt-container", Vertical)
            gantt_container.remove_children()
            if events:
                gantt_container.mount(GanttChart(events))
            else:
                gantt_container.mount(Static("[#484f58]No timeline data[/]"))
        except Exception:
            pass

        # Update event table
        event_table = self.query_one("#event-table", DataTable)
        event_table.clear()
        for ev in events:
            etype = ev.get("type", "\u2014")
            ename = ev.get("name", "\u2014")
            etime = ev.get("time") or ev.get("timestamp", "\u2014")
            if isinstance(etime, str) and "T" in etime:
                etime = etime.split("T")[1][:12]

            type_color = (
                "#3fb950"
                if "COMPLETED" in etype
                else (
                    "#f85149"
                    if "FAILED" in etype
                    else ("#d29922" if "STARTED" in etype else "#8b949e")
                )
            )
            event_table.add_row(f"[{type_color}]{etype}[/]", ename, etime)

    def _show_event_detail(self, row_index: int) -> None:
        """Show details for the selected event."""
        if not self._current_execution:
            return
        events = self._current_execution.get("events", [])
        if 0 <= row_index < len(events):
            event = events[row_index]
            lines = []
            for key, value in event.items():
                display_val = (
                    truncate_json(value, max_length=60)
                    if isinstance(value, (dict, list))
                    else str(value)
                )
                lines.append(f"[#8b949e]{key}:[/] {display_val}")
            try:
                self.query_one("#event-detail-content", Static).update("\n".join(lines))
            except Exception:
                pass

    def action_back_to_list(self) -> None:
        """Return to list mode."""
        if not self._detail_visible:
            return
        self._detail_visible = False
        self._current_execution = None
        self.query_one("#exec-table", DataTable).display = True
        self.query_one("#exec-detail", Vertical).display = False

    def action_view_data(self) -> None:
        """Open JSON viewer for current execution's input/output."""
        if self._current_execution:
            data = {
                "input": self._current_execution.get("input"),
                "output": self._current_execution.get("output"),
            }
            self.app.push_screen(JsonViewerModal("Execution Data", data))
