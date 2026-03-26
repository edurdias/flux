from __future__ import annotations

from typing import Any

from textual.app import App, ComposeResult
from textual.widgets import Footer, Header, TabbedContent, TabPane

from flux.client import FluxClient
from flux.console.screens.dashboard import DashboardView
from flux.console.screens.executions import ExecutionsView
from flux.console.screens.logs import LogsView
from flux.console.screens.schedules import SchedulesView
from flux.console.screens.workers import WorkersView
from flux.console.screens.workflows import WorkflowsView
from flux.utils import get_logger

logger = get_logger(__name__)


class FluxConsoleApp(App):
    """Flux Console — Terminal UI for monitoring and managing workflows."""

    TITLE = "Flux Console"
    CSS_PATH = "app.tcss"
    BINDINGS = [
        ("q", "quit", "Quit"),
    ]

    TAB_NAMES = ["Dashboard", "Workflows", "Executions", "Workers", "Schedules", "Logs"]

    def __init__(self, server_url: str):
        super().__init__()
        self.server_url = server_url
        self.client = FluxClient(server_url)
        self.connected = False

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent():
            with TabPane("Dashboard", id="tab-dashboard"):
                yield DashboardView(id="dashboard-view")
            with TabPane("Workflows", id="tab-workflows"):
                yield WorkflowsView(id="workflows-view")
            with TabPane("Executions", id="tab-executions"):
                yield ExecutionsView(id="executions-view")
            with TabPane("Workers", id="tab-workers"):
                yield WorkersView(id="workers-view")
            with TabPane("Schedules", id="tab-schedules"):
                yield SchedulesView(id="schedules-view")
            with TabPane("Logs", id="tab-logs"):
                yield LogsView(id="logs-view")
        yield Footer()

    async def on_mount(self) -> None:
        self.set_interval(10.0, self._poll_health)
        self.set_interval(3.0, self._poll_active_view)
        await self._poll_health()
        await self._poll_active_view()
        # Set client references for views that need them
        try:
            self.query_one("#workflows-view", WorkflowsView)._client = self.client
        except Exception:
            pass
        try:
            self.query_one("#schedules-view", SchedulesView)._client = self.client
        except Exception:
            pass

    async def _poll_active_view(self) -> None:
        """Poll data only for the currently active tab."""
        if not self.connected:
            return
        try:
            active = self.query_one(TabbedContent).active
            if active == "tab-dashboard":
                await self._poll_dashboard()
            elif active == "tab-workflows":
                await self._poll_workflows()
            elif active == "tab-executions":
                await self._poll_executions()
            elif active == "tab-workers":
                await self._poll_workers()
            elif active == "tab-schedules":
                await self._poll_schedules()
            elif active == "tab-logs":
                await self._poll_logs()
        except Exception as e:
            logger.debug(f"Poll error: {e}")

    async def _poll_health(self) -> None:
        result = await self.client.health_check()
        self.connected = result is not None
        if self.connected:
            self.sub_title = f"● {self.server_url}"
        else:
            self.sub_title = f"○ {self.server_url} (disconnected)"

    async def _poll_dashboard(self) -> None:
        if not self.connected:
            return
        try:
            data: dict[str, Any] = {}
            data["workflows"] = await self.client.list_workflows()
            data["executions"] = await self.client.list_executions(limit=10)
            data["workers"] = await self.client.list_workers()
            data["schedules"] = await self.client.list_schedules()
            try:
                dashboard = self.query_one("#dashboard-view", DashboardView)
                dashboard.update_data(data)
            except Exception:
                pass
        except Exception as e:
            logger.debug(f"Dashboard poll error: {e}")

    async def _poll_executions(self) -> None:
        if not self.connected:
            return
        try:
            data = await self.client.list_executions(limit=50)
            try:
                view = self.query_one("#executions-view", ExecutionsView)
                view.update_data(data)
            except Exception:
                pass
        except Exception as e:
            logger.debug(f"Executions poll error: {e}")

    async def _poll_workers(self) -> None:
        if not self.connected:
            return
        try:
            workers = await self.client.list_workers()
            try:
                workers_view = self.query_one("#workers-view", WorkersView)
                workers_view.update_data(workers)
            except Exception:
                pass
        except Exception as e:
            logger.debug(f"Workers poll error: {e}")

    async def _poll_workflows(self) -> None:
        if not self.connected:
            return
        try:
            workflows = await self.client.list_workflows()
            try:
                view = self.query_one("#workflows-view", WorkflowsView)
                view.update_data(workflows)
                # If a workflow is selected, fetch its executions
                if view._selected_workflow:
                    name = view._selected_workflow.get("name", "")
                    execs = await self.client.get_workflow_executions(name, limit=10)
                    view.update_executions(execs)
            except Exception:
                pass
        except Exception as e:
            logger.debug(f"Workflows poll error: {e}")

    async def _poll_schedules(self) -> None:
        if not self.connected:
            return
        try:
            schedules = await self.client.list_schedules()
            try:
                view = self.query_one("#schedules-view", SchedulesView)
                view.update_data(schedules)
                if view._selected_schedule:
                    sid = view._selected_schedule.get("id", "")
                    history = await self.client.get_schedule_history(sid, limit=10)
                    view.update_history(history)
            except Exception:
                pass
        except Exception as e:
            logger.debug(f"Schedules poll error: {e}")

    async def _poll_logs(self) -> None:
        if not self.connected:
            return
        try:
            data = await self.client.list_executions(limit=20)
            executions = data.get("executions", [])
            # Fetch detailed events for recent executions
            detailed_execs = []
            for ex in executions[:10]:
                exec_id = ex.get("execution_id", "")
                if exec_id:
                    try:
                        detail = await self.client.get_execution(exec_id, detailed=True)
                        detailed_execs.append(detail)
                    except Exception:
                        detailed_execs.append(ex)
            try:
                view = self.query_one("#logs-view", LogsView)
                view.update_data(detailed_execs)
            except Exception:
                pass
        except Exception as e:
            logger.debug(f"Logs poll error: {e}")

    async def action_quit(self) -> None:
        await self.client.close()
        self.exit()
