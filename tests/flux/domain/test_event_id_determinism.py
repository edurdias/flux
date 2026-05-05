"""Replay-determinism guard for ExecutionEvent IDs.

Python's built-in hash() is randomized per-process under ASLR, which made
event IDs differ across processes for the same logical event — breaking
ContextManager._get_additional_events deduplication on replay. The IDs
are now SHA256-derived, so the assertions below should hold across any
fresh interpreter run.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timezone

from flux.domain.events import ExecutionEvent, ExecutionEventType


def _make_event() -> ExecutionEvent:
    return ExecutionEvent(
        type=ExecutionEventType.TASK_COMPLETED,
        source_id="task-123",
        name="my_task",
        value={"answer": 42, "nested": {"a": [1, 2, 3]}},
        time=datetime(2026, 5, 4, 12, 0, 0, tzinfo=timezone.utc),
    )


def test_event_id_is_stable_across_calls():
    a = _make_event()
    b = _make_event()
    assert a.id == b.id


def test_event_id_differs_when_value_changes():
    base = _make_event()
    other = ExecutionEvent(
        type=ExecutionEventType.TASK_COMPLETED,
        source_id="task-123",
        name="my_task",
        value={"answer": 43},
        time=base.time,
    )
    assert base.id != other.id


def test_event_id_is_stable_across_processes():
    """Spawn a fresh interpreter and confirm the same event computes the same ID."""
    expected = _make_event().id

    code = (
        "from datetime import datetime, timezone\n"
        "from flux.domain.events import ExecutionEvent, ExecutionEventType\n"
        "e = ExecutionEvent(\n"
        "    type=ExecutionEventType.TASK_COMPLETED,\n"
        "    source_id='task-123',\n"
        "    name='my_task',\n"
        "    value={'answer': 42, 'nested': {'a': [1, 2, 3]}},\n"
        "    time=datetime(2026, 5, 4, 12, 0, 0, tzinfo=timezone.utc),\n"
        ")\n"
        "print(e.id)\n"
    )
    out = subprocess.check_output([sys.executable, "-c", code], text=True).strip()
    assert out == expected
