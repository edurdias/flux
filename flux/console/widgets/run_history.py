from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Static


BLOCK_CHARS = " ▁▂▃▄▅▆▇█"

STATE_COLORS = {
    "COMPLETED": "#3fb950",
    "FAILED": "#f85149",
    "RUNNING": "#d29922",
    "CANCELLED": "#484f58",
    "PAUSED": "#bc8cff",
}


def _get_duration_seconds(execution: dict[str, Any]) -> float:
    """Extract duration from execution data."""
    duration = execution.get("duration")
    if isinstance(duration, (int, float)):
        return float(duration)
    events = execution.get("events", [])
    # Try to compute from events
    start_time = None
    end_time = None
    for ev in events:
        etime = ev.get("time") or ev.get("timestamp", "")
        if not etime:
            continue
        if "WORKFLOW_STARTED" in ev.get("type", ""):
            start_time = etime
        elif "WORKFLOW_COMPLETED" in ev.get("type", "") or "WORKFLOW_FAILED" in ev.get("type", ""):
            end_time = etime
    if start_time and end_time:
        from datetime import datetime

        try:
            s = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
            e = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
            return (e - s).total_seconds()
        except (ValueError, TypeError):
            pass
    return 0.0


def _extract_task_names(executions: list[dict[str, Any]]) -> list[str]:
    """Extract unique task names across all executions."""
    names: list[str] = []
    seen: set[str] = set()
    for ex in executions:
        for ev in ex.get("events", []):
            etype = ev.get("type", "")
            name = ev.get("name", "")
            if "TASK_" in etype and name and name not in seen:
                seen.add(name)
                names.append(name)
    return names


def _get_task_state(execution: dict[str, Any], task_name: str) -> str | None:
    """Get the final state of a task in an execution."""
    state = None
    for ev in execution.get("events", []):
        name = ev.get("name", "")
        etype = ev.get("type", "")
        if name == task_name:
            if "COMPLETED" in etype:
                state = "COMPLETED"
            elif "FAILED" in etype:
                state = "FAILED"
            elif "STARTED" in etype:
                state = "RUNNING"
    return state


class RunHistoryChart(Widget):
    """Databricks-style run history chart with bars and task swim lanes."""

    DEFAULT_CSS = """
    RunHistoryChart {
        height: auto;
        min-height: 4;
        padding: 0 1;
    }
    """

    def __init__(self, executions: list[dict[str, Any]], max_columns: int = 30, **kwargs):
        super().__init__(**kwargs)
        self.executions = executions[:max_columns]
        self.max_columns = max_columns

    def compose(self) -> ComposeResult:
        if not self.executions:
            yield Static("[#484f58]No execution history[/]")
            return

        # Calculate durations and normalize
        durations = [_get_duration_seconds(ex) for ex in self.executions]
        max_duration = max(durations) if durations else 1.0
        if max_duration <= 0:
            max_duration = 1.0

        # Build bar row (variable height)
        bar_chars = []
        for i, ex in enumerate(self.executions):
            state = ex.get("state", "COMPLETED")
            color = STATE_COLORS.get(state, "#484f58")
            duration = durations[i]
            # Map duration to block character index (1-8)
            level = int(duration / max_duration * 8)
            level = max(1, min(8, level))
            char = BLOCK_CHARS[level]
            bar_chars.append(f"[{color}]{char}[/]")

        yield Static("".join(bar_chars))

        # Separator
        sep = "[#30363d]" + "─" * len(self.executions) + "[/]"
        yield Static(sep)

        # Task swim lanes
        task_names = _extract_task_names(self.executions)
        for task_name in task_names:
            cells = []
            for ex in self.executions:
                task_state = _get_task_state(ex, task_name)
                if task_state:
                    color = STATE_COLORS.get(task_state, "#484f58")
                    cells.append(f"[{color}]■[/]")
                else:
                    cells.append("[#1c2128]·[/]")

            display_name = task_name[:8]
            yield Static(f"[#484f58]{display_name:<8}[/] {''.join(cells)}")
