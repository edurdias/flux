from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widget import Widget
from textual.widgets import Static

from flux.console.widgets.stat_card import StatCard


class DashboardView(Widget):
    """Dashboard home view showing system health at a glance."""

    DEFAULT_CSS = """
    DashboardView {
        layout: vertical;
        height: 1fr;
    }
    DashboardView #stat-row {
        layout: horizontal;
        height: 5;
        margin: 1 0;
    }
    DashboardView #main-panels {
        layout: horizontal;
        height: 1fr;
    }
    DashboardView #recent-executions {
        width: 1fr;
        border: solid #30363d;
        margin: 0 1 0 0;
        padding: 1;
    }
    DashboardView #right-column {
        width: 1fr;
        layout: vertical;
    }
    DashboardView #worker-health {
        border: solid #30363d;
        padding: 1;
        height: 1fr;
    }
    DashboardView #upcoming-schedules {
        border: solid #30363d;
        padding: 1;
        height: auto;
        margin-top: 1;
    }
    DashboardView .panel-title {
        color: #8b949e;
        text-style: bold;
        margin-bottom: 1;
    }
    DashboardView .exec-row {
        height: 1;
    }
    """

    def compose(self) -> ComposeResult:
        with Horizontal(id="stat-row"):
            yield StatCard("WORKFLOWS", "—", "registered", color="info", id="stat-workflows")
            yield StatCard("RUNNING", "—", "executions", color="warning", id="stat-running")
            yield StatCard("WORKERS", "—", "online", color="success", id="stat-workers")
            yield StatCard("SCHEDULES", "—", "active", color="purple", id="stat-schedules")

        with Horizontal(id="main-panels"):
            with Vertical(id="recent-executions"):
                yield Static("RECENT EXECUTIONS", classes="panel-title")
                yield Static("Loading...", id="exec-list")

            with Vertical(id="right-column"):
                with Vertical(id="worker-health"):
                    yield Static("WORKER HEALTH", classes="panel-title")
                    yield Static("Loading...", id="worker-list")

                with Vertical(id="upcoming-schedules"):
                    yield Static("UPCOMING SCHEDULES", classes="panel-title")
                    yield Static("Loading...", id="schedule-list")

    def update_data(self, data: dict[str, Any]) -> None:
        """Update all dashboard panels with fresh data."""
        workflows = data.get("workflows", [])
        executions = data.get("executions", {})
        workers = data.get("workers", [])
        schedules = data.get("schedules", [])

        # Update stat cards
        try:
            self.query_one("#stat-workflows", StatCard).update_value(str(len(workflows)))
        except Exception:
            pass

        running = [e for e in executions.get("executions", []) if e.get("state") == "RUNNING"]
        try:
            self.query_one("#stat-running", StatCard).update_value(str(len(running)))
        except Exception:
            pass

        try:
            self.query_one("#stat-workers", StatCard).update_value(str(len(workers)))
        except Exception:
            pass

        active_schedules = [s for s in schedules if s.get("status") == "active"]
        try:
            self.query_one("#stat-schedules", StatCard).update_value(str(len(active_schedules)))
        except Exception:
            pass

        # Update recent executions
        exec_lines = []
        for ex in executions.get("executions", [])[:10]:
            state = ex.get("state", "")
            name = ex.get("workflow_name", "unknown")
            icon = {"COMPLETED": "\u2713", "FAILED": "\u2717", "RUNNING": "\u25b6"}.get(
                state, "\u25cb"
            )
            color = {"COMPLETED": "success", "FAILED": "error", "RUNNING": "warning"}.get(
                state, "muted"
            )
            worker = ex.get("worker_name", "\u2014") or "\u2014"
            exec_lines.append(f"[{color}]{icon}[/] {name}  {worker}")

        try:
            self.query_one("#exec-list", Static).update(
                "\n".join(exec_lines) if exec_lines else "No executions"
            )
        except Exception:
            pass

        # Update worker health
        worker_lines = []
        for w in workers:
            name = w.get("name", "unknown")
            resources = w.get("resources") or {}
            cpu_total = resources.get("cpu_total", 0)
            cpu_avail = resources.get("cpu_available", 0)
            cpu_used = cpu_total - cpu_avail
            pct = int((cpu_used / cpu_total * 100) if cpu_total > 0 else 0)
            bar_len = 10
            filled = int(bar_len * pct / 100)
            bar = "\u2588" * filled + "\u2591" * (bar_len - filled)
            color = "success" if pct < 60 else ("warning" if pct < 85 else "error")
            worker_lines.append(f"{name:<12} [{color}]{bar}[/]  {pct}%")

        try:
            self.query_one("#worker-list", Static).update(
                "\n".join(worker_lines) if worker_lines else "No workers"
            )
        except Exception:
            pass

        # Update upcoming schedules
        sched_lines = []
        for s in active_schedules[:5]:
            name = s.get("name", "unknown")
            next_run = s.get("next_run_at", "\u2014")
            sched_lines.append(f"[purple]{name}[/]  {next_run}")

        try:
            self.query_one("#schedule-list", Static).update(
                "\n".join(sched_lines) if sched_lines else "No active schedules"
            )
        except Exception:
            pass
