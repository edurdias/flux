from __future__ import annotations

from textual.app import App, ComposeResult
from textual.widgets import Footer, Header, Static, TabbedContent, TabPane

from flux.console.client import FluxClient
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
                yield Static("Dashboard — loading...", id="dashboard-placeholder")
            with TabPane("Workflows", id="tab-workflows"):
                yield Static("Workflows — loading...", id="workflows-placeholder")
            with TabPane("Executions", id="tab-executions"):
                yield Static("Executions — loading...", id="executions-placeholder")
            with TabPane("Workers", id="tab-workers"):
                yield Static("Workers — loading...", id="workers-placeholder")
            with TabPane("Schedules", id="tab-schedules"):
                yield Static("Schedules — loading...", id="schedules-placeholder")
            with TabPane("Logs", id="tab-logs"):
                yield Static("Logs — loading...", id="logs-placeholder")
        yield Footer()

    async def on_mount(self) -> None:
        self.poll_health = self.set_interval(10.0, self._poll_health)
        await self._poll_health()

    async def _poll_health(self) -> None:
        result = await self.client.health_check()
        self.connected = result is not None
        if self.connected:
            self.sub_title = f"● {self.server_url}"
        else:
            self.sub_title = f"○ {self.server_url} (disconnected)"

    async def action_quit(self) -> None:
        await self.client.close()
        self.exit()
