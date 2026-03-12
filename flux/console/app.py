from __future__ import annotations

from textual.app import App, ComposeResult
from textual.widgets import Footer, Header, Static, TabbedContent, TabPane

from flux.console.client import FluxClient
from flux.console.screens.dashboard import DashboardView
from flux.console.screens.workers import WorkersView
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
                yield Static("Workflows — loading...", id="workflows-placeholder")
            with TabPane("Executions", id="tab-executions"):
                yield Static("Executions — loading...", id="executions-placeholder")
            with TabPane("Workers", id="tab-workers"):
                yield WorkersView(id="workers-view")
            with TabPane("Schedules", id="tab-schedules"):
                yield Static("Schedules — loading...", id="schedules-placeholder")
            with TabPane("Logs", id="tab-logs"):
                yield Static("Logs — loading...", id="logs-placeholder")
        yield Footer()

    async def on_mount(self) -> None:
        self.poll_health = self.set_interval(10.0, self._poll_health)
        self.set_interval(5.0, self._poll_dashboard)
        self.set_interval(5.0, self._poll_workers)
        await self._poll_health()

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
            data = {}
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

    async def action_quit(self) -> None:
        await self.client.close()
        self.exit()
