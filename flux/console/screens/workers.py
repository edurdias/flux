from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.containers import Grid
from textual.widget import Widget
from textual.widgets import Static


def _format_bytes(b: float) -> str:
    """Format bytes to human-readable string."""
    if b <= 0:
        return "0"
    for unit in ["B", "K", "M", "G", "T"]:
        if b < 1024:
            return f"{b:.0f}{unit}"
        b /= 1024
    return f"{b:.0f}P"


class WorkerCard(Widget):
    """A card displaying a single worker's status and resources."""

    DEFAULT_CSS = """
    WorkerCard {
        border: solid #30363d;
        padding: 1;
        height: auto;
        min-height: 8;
    }
    WorkerCard.selected {
        border: solid #58a6ff;
        background: #1f6feb11;
    }
    WorkerCard.offline {
        opacity: 50%;
    }
    WorkerCard .worker-header {
        height: 1;
        margin-bottom: 1;
    }
    WorkerCard .res-row {
        height: 1;
    }
    WorkerCard .worker-task {
        background: #161b22;
        margin-top: 1;
        padding: 0 1;
        height: 1;
    }
    """

    def __init__(self, worker_data: dict[str, Any], **kwargs):
        super().__init__(**kwargs)
        self.worker_data = worker_data
        if worker_data.get("status") != "online":
            self.add_class("offline")

    def compose(self) -> ComposeResult:
        w = self.worker_data
        name = w.get("name", "unknown")
        status = w.get("status", "offline")
        runtime = w.get("runtime") or {}
        resources = w.get("resources") or {}
        os_name = runtime.get("os_name", "")
        py_ver = runtime.get("python_version", "")

        dot_color = "#3fb950" if status == "online" else "#f85149"
        yield Static(
            f"[bold]{name}[/]  [{dot_color}]\u25cf[/]  [#484f58]{os_name} \u00b7 Python {py_ver}[/]",
            classes="worker-header",
        )

        cpu_total = resources.get("cpu_total", 0)
        cpu_avail = resources.get("cpu_available", 0)
        mem_total = resources.get("memory_total", 0)
        mem_avail = resources.get("memory_available", 0)
        disk_total = resources.get("disk_total", 0)
        disk_free = resources.get("disk_free", 0)
        gpus = resources.get("gpus", [])

        for label, used, total, fmt in [
            (
                "CPU",
                cpu_total - cpu_avail,
                cpu_total,
                lambda u, t: f"{u:.1f}/{t:.0f}",
            ),
            (
                "MEM",
                mem_total - mem_avail,
                mem_total,
                lambda u, t: f"{_format_bytes(u)}/{_format_bytes(t)}",
            ),
            (
                "DISK",
                disk_total - disk_free,
                disk_total,
                lambda u, t: f"{_format_bytes(u)}/{_format_bytes(t)}",
            ),
        ]:
            if total > 0:
                pct = int(used / total * 100)
                bar_len = 10
                filled = int(bar_len * pct / 100)
                bar = "\u2588" * filled + "\u2591" * (bar_len - filled)
                color = "3fb950" if pct < 60 else ("d29922" if pct < 85 else "f85149")
                yield Static(
                    f"[#8b949e]{label:<5}[/] [#{color}]{bar}[/]"
                    f"  [#8b949e]{fmt(used, total)}  {pct}%[/]",
                    classes="res-row",
                )

        for gpu in gpus:
            gpu_total = gpu.get("memory_total", 0)
            gpu_avail = gpu.get("memory_available", 0)
            gpu_used = gpu_total - gpu_avail
            if gpu_total > 0:
                pct = int(gpu_used / gpu_total * 100)
                bar_len = 10
                filled = int(bar_len * pct / 100)
                bar = "\u2588" * filled + "\u2591" * (bar_len - filled)
                yield Static(
                    f"[#8b949e]GPU  [/] [#bc8cff]{bar}[/]"
                    f"  [#8b949e]{gpu_used:.0f}/{gpu_total:.0f}M  {pct}%[/]",
                    classes="res-row",
                )

        if status == "online":
            yield Static("[#3fb950]Idle[/]", classes="worker-task")
        else:
            yield Static("[#f85149]Offline[/]", classes="worker-task")


class WorkersView(Widget):
    """Workers view showing worker health cards in a grid."""

    DEFAULT_CSS = """
    WorkersView {
        layout: vertical;
        height: 1fr;
    }
    WorkersView #workers-summary {
        height: 1;
        margin-bottom: 1;
    }
    WorkersView #workers-grid {
        layout: grid;
        grid-size: 2;
        grid-gutter: 1;
        height: 1fr;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static("Loading workers...", id="workers-summary")
        yield Grid(id="workers-grid")

    def update_data(self, workers: list[dict[str, Any]]) -> None:
        online = sum(1 for w in workers if w.get("status") == "online")
        offline = len(workers) - online

        try:
            self.query_one("#workers-summary", Static).update(
                f"[#3fb950]\u25cf[/] {online} online  [#f85149]\u25cf[/] {offline} offline",
            )
        except Exception:
            pass

        try:
            grid = self.query_one("#workers-grid", Grid)
            grid.remove_children()
            for w in workers:
                grid.mount(WorkerCard(w))
        except Exception:
            pass
