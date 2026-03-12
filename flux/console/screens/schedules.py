from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Button, DataTable, Static

from flux.console.widgets.schedule_editor import ScheduleEditorModal
from flux.console.widgets.stat_card import StatCard


class ConfirmDeleteModal(ModalScreen[bool]):
    """Confirmation dialog for deleting a schedule."""

    DEFAULT_CSS = """
    ConfirmDeleteModal {
        align: center middle;
    }
    ConfirmDeleteModal #confirm-dialog {
        width: 50%;
        height: auto;
        min-height: 7;
        border: solid #f85149;
        background: #161b22;
        padding: 1;
    }
    ConfirmDeleteModal #confirm-buttons {
        layout: horizontal;
        height: 3;
        margin-top: 1;
    }
    ConfirmDeleteModal #confirm-buttons Button {
        margin: 0 1;
    }
    """

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, schedule_name: str, **kwargs):
        super().__init__(**kwargs)
        self.schedule_name = schedule_name

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-dialog"):
            yield Static(f"[bold #f85149]Delete schedule '{self.schedule_name}'?[/]")
            yield Static("[#484f58]This action cannot be undone.[/]")
            with Horizontal(id="confirm-buttons"):
                yield Button("Delete", variant="error", id="delete-btn")
                yield Button("Cancel", variant="default", id="cancel-btn")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "delete-btn")

    def action_cancel(self) -> None:
        self.dismiss(False)


class SchedulesView(Widget):
    """Schedules view with list/detail split."""

    DEFAULT_CSS = """
    SchedulesView {
        layout: horizontal;
        height: 1fr;
    }
    SchedulesView #sched-list-panel {
        width: 2fr;
        border: solid #30363d;
        margin-right: 1;
    }
    SchedulesView #sched-detail-panel {
        width: 3fr;
        layout: vertical;
    }
    SchedulesView #sched-detail-header {
        height: 3;
        border: solid #30363d;
        padding: 0 1;
        margin-bottom: 1;
    }
    SchedulesView #sched-stats-row {
        layout: horizontal;
        height: 5;
        margin-bottom: 1;
    }
    SchedulesView #sched-history {
        border: solid #30363d;
        padding: 1;
        height: 1fr;
    }
    SchedulesView .section-title {
        color: #8b949e;
        text-style: bold;
        height: 1;
        padding: 0 1;
    }
    """

    BINDINGS = [
        ("p", "toggle_pause", "Pause/Resume"),
        ("e", "edit_schedule", "Edit"),
        ("d", "delete_schedule", "Delete"),
    ]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._schedules: list[dict[str, Any]] = []
        self._selected_schedule: dict[str, Any] | None = None
        self._client = None

    def compose(self) -> ComposeResult:
        with Vertical(id="sched-list-panel"):
            yield Static(
                "SCHEDULES  [#484f58]p=Pause  e=Edit  d=Delete[/]",
                classes="section-title",
            )
            table = DataTable(id="sched-table")
            table.cursor_type = "row"
            yield table

        with Vertical(id="sched-detail-panel"):
            yield Static("", id="sched-detail-header")
            with Horizontal(id="sched-stats-row"):
                yield StatCard("STATUS", "\u2014", "", color="info", id="sched-stat-status")
                yield StatCard("RUNS", "\u2014", "total", color="success", id="sched-stat-runs")
                yield StatCard("NEXT RUN", "\u2014", "", color="purple", id="sched-stat-next")
            with Vertical(id="sched-history"):
                yield Static("EXECUTION HISTORY", classes="section-title")
                yield Static("Select a schedule", id="sched-history-list")

    def on_mount(self) -> None:
        table = self.query_one("#sched-table", DataTable)
        table.add_columns("Name", "Status", "Expression", "Next Run")

    def update_data(self, schedules: list[dict[str, Any]]) -> None:
        self._schedules = schedules
        table = self.query_one("#sched-table", DataTable)
        table.clear()

        for s in schedules:
            name = s.get("name", "\u2014")
            status = s.get("status", "\u2014")
            expression = s.get("expression", "\u2014")
            next_run = s.get("next_run_at", "\u2014")
            if isinstance(next_run, str) and "T" in next_run:
                next_run = next_run.split("T")[1][:8]

            status_color = (
                "#3fb950"
                if status == "active"
                else ("#d29922" if status == "paused" else "#8b949e")
            )
            table.add_row(name, f"[{status_color}]{status}[/]", expression, next_run)

        if self._selected_schedule:
            sid = self._selected_schedule.get("id")
            for s in schedules:
                if s.get("id") == sid:
                    self._update_detail(s)
                    break

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id != "sched-table":
            return
        row_index = event.cursor_row
        if 0 <= row_index < len(self._schedules):
            sched = self._schedules[row_index]
            self._selected_schedule = sched
            self._update_detail(sched)

    def _update_detail(self, schedule: dict[str, Any]) -> None:
        name = schedule.get("name", "\u2014")
        status = schedule.get("status", "\u2014")
        expression = schedule.get("expression", "\u2014")
        workflow = schedule.get("workflow_name", "\u2014")
        created = schedule.get("created_at", "\u2014")

        status_color = "#3fb950" if status == "active" else "#d29922"

        try:
            self.query_one("#sched-detail-header", Static).update(
                f"[bold]{name}[/]  [{status_color}]{status}[/]\n"
                f"[#484f58]Workflow:[/] {workflow}  "
                f"[#484f58]Expression:[/] {expression}  "
                f"[#484f58]Created:[/] {created}",
            )
        except Exception:
            pass

        try:
            self.query_one("#sched-stat-status", StatCard).update_value(status)
        except Exception:
            pass

        run_count = schedule.get("run_count", 0)
        try:
            self.query_one("#sched-stat-runs", StatCard).update_value(str(run_count))
        except Exception:
            pass

        next_run = schedule.get("next_run_at", "\u2014")
        if isinstance(next_run, str) and "T" in next_run:
            next_run = next_run.split("T")[1][:8]
        try:
            self.query_one("#sched-stat-next", StatCard).update_value(str(next_run))
        except Exception:
            pass

    def update_history(self, history: list[dict[str, Any]]) -> None:
        lines = []
        for h in history[:10]:
            state = h.get("state", "")
            icon = {"COMPLETED": "\u2713", "FAILED": "\u2717", "RUNNING": "\u25b6"}.get(
                state,
                "\u25cb",
            )
            color = {"COMPLETED": "#3fb950", "FAILED": "#f85149", "RUNNING": "#d29922"}.get(
                state,
                "#8b949e",
            )
            started = h.get("started_at", "\u2014") or "\u2014"
            if isinstance(started, str) and "T" in started:
                started = started.split("T")[1][:8]
            duration = h.get("duration", "\u2014")
            if isinstance(duration, (int, float)):
                duration = f"{duration:.1f}s"
            lines.append(f"[{color}]{icon}[/] {started}  {duration}")

        try:
            self.query_one("#sched-history-list", Static).update(
                "\n".join(lines) if lines else "No history",
            )
        except Exception:
            pass

    async def action_toggle_pause(self) -> None:
        if not self._selected_schedule or not self._client:
            return
        sid = self._selected_schedule.get("id", "")
        status = self._selected_schedule.get("status", "")
        try:
            if status == "active":
                await self._client.pause_schedule(sid)
            else:
                await self._client.resume_schedule(sid)
        except Exception:
            pass

    async def action_edit_schedule(self) -> None:
        if not self._selected_schedule or not self._client:
            return

        def handle_result(result: dict | None) -> None:
            if result and self._client:
                self.app.call_later(self._do_update_schedule, result)

        self.app.push_screen(ScheduleEditorModal(self._selected_schedule), callback=handle_result)

    async def _do_update_schedule(self, data: dict) -> None:
        if self._client:
            sid = data.get("schedule_id", "")
            try:
                await self._client.update_schedule(sid, {"expression": data.get("expression", "")})
            except Exception:
                pass

    async def action_delete_schedule(self) -> None:
        if not self._selected_schedule or not self._client:
            return
        name = self._selected_schedule.get("name", "")

        def handle_confirm(confirmed: bool) -> None:
            if confirmed and self._client and self._selected_schedule:
                sid = self._selected_schedule.get("id", "")
                self.app.call_later(self._do_delete_schedule, sid)

        self.app.push_screen(ConfirmDeleteModal(name), callback=handle_confirm)

    async def _do_delete_schedule(self, schedule_id: str) -> None:
        if self._client:
            try:
                await self._client.delete_schedule(schedule_id)
                self._selected_schedule = None
            except Exception:
                pass
