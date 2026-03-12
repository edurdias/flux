from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import DataTable, Input, Static

from flux.console.widgets.stat_card import StatCard


class VersionListModal(ModalScreen):
    """Modal showing workflow version history."""

    DEFAULT_CSS = """
    VersionListModal {
        align: center middle;
    }
    VersionListModal #version-dialog {
        width: 60%;
        height: 60%;
        border: solid #30363d;
        background: #161b22;
        padding: 1;
    }
    VersionListModal #version-title {
        dock: top;
        height: 1;
        color: #8b949e;
        text-style: bold;
    }
    """

    BINDINGS = [("escape", "dismiss", "Close")]

    def __init__(self, workflow_name: str, versions: list[dict[str, Any]], **kwargs):
        super().__init__(**kwargs)
        self.workflow_name = workflow_name
        self.versions = versions

    def compose(self) -> ComposeResult:
        with Vertical(id="version-dialog"):
            yield Static(f" Versions: {self.workflow_name} (Esc to close)", id="version-title")
            table = DataTable(id="version-table")
            table.cursor_type = "row"
            yield table

    def on_mount(self) -> None:
        table = self.query_one("#version-table", DataTable)
        table.add_columns("Version", "Created", "Hash")
        for v in self.versions:
            ver = str(v.get("version", "—"))
            created = str(v.get("created_at", "—"))
            if "T" in created:
                created = created.split("T")[0]
            hash_val = str(v.get("hash", "—"))[:12]
            table.add_row(ver, created, hash_val)


class RunWorkflowModal(ModalScreen[dict | None]):
    """Modal for entering workflow input data."""

    DEFAULT_CSS = """
    RunWorkflowModal {
        align: center middle;
    }
    RunWorkflowModal #run-dialog {
        width: 60%;
        height: auto;
        min-height: 8;
        border: solid #30363d;
        background: #161b22;
        padding: 1;
    }
    RunWorkflowModal #run-title {
        height: 1;
        color: #8b949e;
        text-style: bold;
        margin-bottom: 1;
    }
    RunWorkflowModal #run-help {
        height: 1;
        color: #484f58;
        margin-top: 1;
    }
    """

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, workflow_name: str, **kwargs):
        super().__init__(**kwargs)
        self.workflow_name = workflow_name

    def compose(self) -> ComposeResult:
        with Vertical(id="run-dialog"):
            yield Static(f" Run: {self.workflow_name}", id="run-title")
            yield Input(
                placeholder='Enter JSON input (e.g. {"key": "value"}) or leave empty',
                id="run-input",
            )
            yield Static("[#484f58]Enter=Submit  Esc=Cancel[/]", id="run-help")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        import json

        text = event.value.strip()
        if not text:
            self.dismiss(None)
        else:
            try:
                data = json.loads(text)
                self.dismiss(data)
            except json.JSONDecodeError:
                self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class WorkflowsView(Widget):
    """Workflows view with list/detail split."""

    DEFAULT_CSS = """
    WorkflowsView {
        layout: horizontal;
        height: 1fr;
    }
    WorkflowsView #wf-list-panel {
        width: 2fr;
        border: solid #30363d;
        margin-right: 1;
    }
    WorkflowsView #wf-detail-panel {
        width: 3fr;
        layout: vertical;
    }
    WorkflowsView #wf-detail-header {
        height: 3;
        border: solid #30363d;
        padding: 0 1;
        margin-bottom: 1;
    }
    WorkflowsView #wf-stats-row {
        layout: horizontal;
        height: 5;
        margin-bottom: 1;
    }
    WorkflowsView #wf-recent-execs {
        border: solid #30363d;
        padding: 1;
        height: 1fr;
    }
    WorkflowsView .section-title {
        color: #8b949e;
        text-style: bold;
        height: 1;
        padding: 0 1;
    }
    WorkflowsView .detail-placeholder {
        color: #484f58;
        content-align: center middle;
        height: 1fr;
    }
    """

    BINDINGS = [
        ("r", "run_workflow", "Run"),
        ("v", "view_versions", "Versions"),
    ]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._workflows: list[dict[str, Any]] = []
        self._selected_workflow: dict[str, Any] | None = None
        self._client = None  # Set by app after mounting

    def compose(self) -> ComposeResult:
        with Vertical(id="wf-list-panel"):
            yield Static("WORKFLOWS  [#484f58]r=Run  v=Versions[/]", classes="section-title")
            table = DataTable(id="wf-table")
            table.cursor_type = "row"
            yield table

        with Vertical(id="wf-detail-panel"):
            yield Static("", id="wf-detail-header")
            with Horizontal(id="wf-stats-row"):
                yield StatCard("RUNS", "—", "total", color="info", id="wf-stat-runs")
                yield StatCard("SUCCESS", "—", "rate", color="success", id="wf-stat-success")
                yield StatCard("VERSION", "—", "current", color="purple", id="wf-stat-version")
            with Vertical(id="wf-recent-execs"):
                yield Static("RECENT EXECUTIONS", classes="section-title")
                yield Static("Select a workflow", id="wf-exec-list")

    def on_mount(self) -> None:
        table = self.query_one("#wf-table", DataTable)
        table.add_columns("Name", "Ver", "Runs")

    def update_data(
        self,
        workflows: list[dict[str, Any]],
        executions: dict[str, Any] | None = None,
    ) -> None:
        """Update workflow list."""
        self._workflows = workflows
        table = self.query_one("#wf-table", DataTable)
        table.clear()

        for wf in workflows:
            name = wf.get("name", "—")
            version = str(wf.get("version", "—"))
            runs = str(wf.get("execution_count", 0))
            table.add_row(name, version, runs)

        # If we had a selection, try to refresh it
        if self._selected_workflow:
            name = self._selected_workflow.get("name")
            for wf in workflows:
                if wf.get("name") == name:
                    self._update_detail(wf)
                    break

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id != "wf-table":
            return
        row_index = event.cursor_row
        if 0 <= row_index < len(self._workflows):
            wf = self._workflows[row_index]
            self._selected_workflow = wf
            self._update_detail(wf)

    def _update_detail(self, wf: dict[str, Any]) -> None:
        """Update the detail panel for the selected workflow."""
        name = wf.get("name", "—")
        version = wf.get("version", "—")
        description = wf.get("description", "")

        try:
            self.query_one("#wf-detail-header", Static).update(
                f"[bold]{name}[/]  [#bc8cff]v{version}[/]\n"
                f"[#484f58]{description or 'No description'}[/]",
            )
        except Exception:
            pass

        try:
            self.query_one("#wf-stat-version", StatCard).update_value(str(version))
        except Exception:
            pass

        exec_count = wf.get("execution_count", 0)
        try:
            self.query_one("#wf-stat-runs", StatCard).update_value(str(exec_count))
        except Exception:
            pass

        success_count = wf.get("success_count", 0)
        rate = f"{(success_count / exec_count * 100):.0f}%" if exec_count > 0 else "—"
        try:
            self.query_one("#wf-stat-success", StatCard).update_value(rate)
        except Exception:
            pass

    def update_executions(self, executions: list[dict[str, Any]]) -> None:
        """Update the recent executions list for the selected workflow."""
        exec_lines = []
        for ex in executions[:10]:
            state = ex.get("state", "")
            icon = {"COMPLETED": "\u2713", "FAILED": "\u2717", "RUNNING": "\u25b6"}.get(
                state,
                "\u25cb",
            )
            color = {
                "COMPLETED": "#3fb950",
                "FAILED": "#f85149",
                "RUNNING": "#d29922",
            }.get(state, "#8b949e")
            duration = ex.get("duration", "—")
            if isinstance(duration, (int, float)):
                duration = f"{duration:.1f}s"
            started = ex.get("started_at", "—") or "—"
            if isinstance(started, str) and "T" in started:
                started = started.split("T")[1][:8]
            exec_lines.append(f"[{color}]{icon}[/] {started}  {duration}")

        try:
            self.query_one("#wf-exec-list", Static).update(
                "\n".join(exec_lines) if exec_lines else "No executions",
            )
        except Exception:
            pass

    async def action_run_workflow(self) -> None:
        if not self._selected_workflow:
            return
        name = self._selected_workflow.get("name", "")

        def handle_result(result: dict | None) -> None:
            if self._client:
                self.app.call_later(self._do_run_workflow, name, result)

        self.app.push_screen(RunWorkflowModal(name), callback=handle_result)

    async def _do_run_workflow(self, name: str, input_data: dict | None) -> None:
        if self._client:
            try:
                await self._client.run_workflow(name, input_data=input_data)
            except Exception:
                pass

    async def action_view_versions(self) -> None:
        if not self._selected_workflow or not self._client:
            return
        name = self._selected_workflow.get("name", "")
        try:
            versions = await self._client.get_workflow_versions(name)
            self.app.push_screen(VersionListModal(name, versions))
        except Exception:
            pass
