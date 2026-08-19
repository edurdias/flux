"""Shared plumbing for the B-series engine benchmarks (#259).

The B series measures the engine rather than the streaming path the T
series characterizes: how long an execution waits before a worker runs it,
how many tasks the whole pipe sustains, and what replaying a long history
costs on resume.

Every figure here is derived from **server-stamped event times**. The
server re-stamps ``ExecutionEvent.time`` at ingest with its own clock
(PLAN.md §0a), so an interval between two events is one clock's arithmetic
-- no skew, no client polling granularity smeared into a latency number.
"""

from __future__ import annotations

from datetime import datetime


def event_times(detail: dict, event_type: str) -> list[datetime]:
    """Every ``event_type`` stamp in one execution's detailed status."""
    return [
        event["time"] if isinstance(event["time"], datetime) else _parse(event["time"])
        for event in detail.get("events", [])
        if event.get("type") == event_type
    ]


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value)


def first_time(detail: dict, event_type: str) -> datetime | None:
    times = event_times(detail, event_type)
    return min(times) if times else None


def last_time(detail: dict, event_type: str) -> datetime | None:
    times = event_times(detail, event_type)
    return max(times) if times else None


def interval_ms(start: datetime | None, end: datetime | None) -> float | None:
    """Milliseconds between two event stamps, or None if either is missing."""
    if start is None or end is None:
        return None
    return (end - start).total_seconds() * 1000


def dispatch_latency_ms(detail: dict) -> float | None:
    """Scheduled → started: what an execution waits before a worker runs it.

    This is the number the dispatch path owns -- the scheduler tick or
    event notification, the claim round trip, and the worker's module load
    -- with the workflow's own body excluded by construction.
    """
    return interval_ms(
        first_time(detail, "WORKFLOW_SCHEDULED"),
        first_time(detail, "WORKFLOW_STARTED"),
    )


def claim_latency_ms(detail: dict) -> float | None:
    """Scheduled → claimed: the dispatch half, without the worker's startup."""
    return interval_ms(
        first_time(detail, "WORKFLOW_SCHEDULED"),
        first_time(detail, "WORKFLOW_CLAIMED"),
    )


def first_task_latency_ms(detail: dict) -> float | None:
    """Scheduled → first task started: what the *workload* waits."""
    return interval_ms(
        first_time(detail, "WORKFLOW_SCHEDULED"),
        first_time(detail, "TASK_STARTED"),
    )


def completed_tasks(detail: dict, name: str | None = None) -> int:
    """TASK_COMPLETED events, optionally only those of one task name.

    The name filter matters on the resume path: ``pause()`` is itself a
    task, so it completes when the execution is resumed and inflates a
    naive count by exactly one -- which reads as "replay re-ran a task"
    when nothing was re-run at all.
    """
    return len(
        [
            event
            for event in detail.get("events", [])
            if event.get("type") == "TASK_COMPLETED" and (name is None or event.get("name") == name)
        ],
    )


def execution_span_s(detail: dict) -> float | None:
    """First to last event stamp of one execution, in seconds."""
    times = [
        event["time"] if isinstance(event["time"], datetime) else _parse(event["time"])
        for event in detail.get("events", [])
    ]
    if len(times) < 2:
        return None
    return (max(times) - min(times)).total_seconds()
