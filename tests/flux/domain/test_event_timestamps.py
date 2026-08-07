"""Event timestamps are timezone-aware UTC at every boundary.

Events are stamped on whichever machine creates them — the server stamps
scheduling/claim events, the worker stamps workflow/task events — so a naive
local timestamp made one execution's log unorderable whenever the two hosts
sat in different zones (#169).
"""

from __future__ import annotations

import time as time_module
from datetime import datetime
from datetime import timedelta
from datetime import timezone

from flux.domain.events import ExecutionEvent
from flux.domain.events import ExecutionEventType


def _event(type=ExecutionEventType.WORKFLOW_STARTED, **kwargs) -> ExecutionEvent:
    return ExecutionEvent(type=type, source_id="src", name="wf", **kwargs)


def test_default_time_is_timezone_aware_utc():
    event = _event()

    assert event.time.tzinfo is not None
    assert event.time.utcoffset() == timedelta(0)


def test_default_time_is_the_current_instant():
    before = datetime.now(timezone.utc)
    event = _event()
    after = datetime.now(timezone.utc)

    assert before <= event.time <= after


def test_naive_time_is_interpreted_as_utc():
    """The one deliberate rule for the migration: naive means UTC.

    Applies to rows written before this change and to older workers still
    checkpointing naive times — both are read back as UTC rather than
    compared against aware values (which raises TypeError).
    """
    event = _event(time=datetime(2026, 8, 7, 0, 57, 11))

    assert event.time == datetime(2026, 8, 7, 0, 57, 11, tzinfo=timezone.utc)


def test_aware_non_utc_time_is_converted_to_utc_preserving_the_instant():
    minus_four = timezone(timedelta(hours=-4))
    event = _event(time=datetime(2026, 8, 6, 20, 57, 11, tzinfo=minus_four))

    assert event.time == datetime(2026, 8, 7, 0, 57, 11, tzinfo=timezone.utc)
    assert event.time.utcoffset() == timedelta(0)


def test_iso_string_time_is_parsed_to_aware_utc():
    """Wire-rebuilt events (ExecutionContext.from_json) carry time as a str."""
    event = _event(time="2026-08-07T00:57:11+00:00")

    assert isinstance(event.time, datetime)
    assert event.time == datetime(2026, 8, 7, 0, 57, 11, tzinfo=timezone.utc)


def test_naive_iso_string_time_is_parsed_as_utc():
    event = _event(time="2026-08-07T00:57:11")

    assert event.time == datetime(2026, 8, 7, 0, 57, 11, tzinfo=timezone.utc)


def test_default_stamp_does_not_depend_on_the_local_timezone(monkeypatch):
    """The #169 reproduction: a UTC server and a UTC-4 worker.

    The worker's WORKFLOW_STARTED genuinely happens after the server's
    WORKFLOW_CLAIMED, but stamping with the local clock made it compare as
    four hours older. Both stamps must land on the true instant regardless of
    where the process runs.
    """
    claimed = _event(type=ExecutionEventType.WORKFLOW_CLAIMED)

    monkeypatch.setenv("TZ", "America/New_York")
    time_module.tzset()
    try:
        started = _event(type=ExecutionEventType.WORKFLOW_STARTED)
    finally:
        monkeypatch.undo()
        time_module.tzset()

    assert datetime.now(timezone.utc) - started.time < timedelta(minutes=1)
    assert started.time > claimed.time
