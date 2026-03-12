from __future__ import annotations

from datetime import datetime
from typing import Any

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Static


def _parse_time(t: str) -> datetime | None:
    """Parse ISO timestamp string."""
    if not t:
        return None
    try:
        return datetime.fromisoformat(t.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _extract_task_spans(
    events: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], datetime | None, datetime | None]:
    """Extract task start/end spans from events."""
    tasks: dict[str, dict[str, Any]] = {}
    workflow_start = None
    workflow_end = None

    for event in events:
        etype = event.get("type", "")
        etime = event.get("time") or event.get("timestamp", "")

        if "WORKFLOW_STARTED" in etype:
            workflow_start = _parse_time(etime)
        elif "WORKFLOW_COMPLETED" in etype or "WORKFLOW_FAILED" in etype:
            workflow_end = _parse_time(etime)
        elif "TASK_STARTED" in etype:
            name = event.get("name", "unknown")
            tasks[name] = {
                "name": name,
                "start": _parse_time(etime),
                "end": None,
                "state": "RUNNING",
            }
        elif "TASK_COMPLETED" in etype:
            name = event.get("name", "unknown")
            if name in tasks:
                tasks[name]["end"] = _parse_time(etime)
                tasks[name]["state"] = "COMPLETED"
        elif "TASK_FAILED" in etype:
            name = event.get("name", "unknown")
            if name in tasks:
                tasks[name]["end"] = _parse_time(etime)
                tasks[name]["state"] = "FAILED"

    return list(tasks.values()), workflow_start, workflow_end


class GanttChart(Widget):
    """Gantt chart showing task timeline using block characters."""

    DEFAULT_CSS = """
    GanttChart {
        height: auto;
        min-height: 3;
        padding: 0 1;
    }
    GanttChart .gantt-header {
        color: #8b949e;
        height: 1;
    }
    GanttChart .gantt-row {
        height: 1;
    }
    """

    def __init__(self, events: list[dict[str, Any]], chart_width: int = 40, **kwargs):
        super().__init__(**kwargs)
        self.events = events
        self.chart_width = chart_width

    def compose(self) -> ComposeResult:
        spans, wf_start, wf_end = _extract_task_spans(self.events)

        if not wf_start or not spans:
            yield Static("[#484f58]No timeline data[/]", classes="gantt-header")
            return

        if wf_end is None:
            wf_end = wf_start
            for span in spans:
                if span.get("end") and span["end"] > wf_end:
                    wf_end = span["end"]

        assert wf_end is not None
        total_duration = (wf_end - wf_start).total_seconds()
        if total_duration <= 0:
            total_duration = 1.0

        yield Static(
            f"[#8b949e]Timeline ({total_duration:.1f}s)[/]",
            classes="gantt-header",
        )

        for span in spans:
            name = span["name"]
            state = span.get("state", "RUNNING")
            start = span.get("start")
            end = span.get("end")

            if start is None:
                continue

            offset_secs = (start - wf_start).total_seconds()
            duration_secs = ((end or wf_end) - start).total_seconds()

            offset_chars = int(offset_secs / total_duration * self.chart_width)
            bar_chars = max(1, int(duration_secs / total_duration * self.chart_width))

            color = {"COMPLETED": "#3fb950", "FAILED": "#f85149", "RUNNING": "#d29922"}.get(
                state,
                "#8b949e",
            )

            padding = " " * offset_chars
            bar = "\u2588" * bar_chars
            remaining = self.chart_width - offset_chars - bar_chars
            empty = "\u2591" * max(0, remaining)

            dur_str = (
                f"{duration_secs:.1f}s" if duration_secs < 60 else f"{duration_secs / 60:.1f}m"
            )

            yield Static(
                f"[#8b949e]{name:<16}[/] {padding}[{color}]{bar}[/]{empty} {dur_str}",
                classes="gantt-row",
            )
