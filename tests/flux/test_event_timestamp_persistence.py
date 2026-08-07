"""Event timestamps survive the persistence and wire round-trips as UTC.

SQLite drops tzinfo on write no matter how the column is declared, so a
freshly-stamped aware event and the same event read back would otherwise
compare as aware-vs-naive and raise (#169).
"""

from __future__ import annotations

from datetime import datetime
from datetime import timedelta
from datetime import timezone

from flux.domain.events import ExecutionEvent
from flux.domain.events import ExecutionEventType
from flux.models import ExecutionEventModel
from flux.servers.models import ExecutionEvent as WireExecutionEvent


def test_to_plain_reattaches_utc_when_the_row_is_naive():
    """SQLite always hands back a naive datetime — including for rows written
    before events became aware."""
    model = ExecutionEventModel(
        execution_id="exec-1",
        source_id="src",
        event_id="abc123",
        type=ExecutionEventType.WORKFLOW_STARTED,
        name="wf",
        time=datetime(2026, 8, 7, 0, 57, 11),
    )

    event = model.to_plain()

    assert event.time == datetime(2026, 8, 7, 0, 57, 11, tzinfo=timezone.utc)
    assert event.time.utcoffset() == timedelta(0)


def test_database_round_trip_preserves_the_instant():
    from examples.hello_world import hello_world
    from flux.context_managers import ContextManager

    ctx = hello_world.run("Joe")
    assert ctx.has_finished and ctx.has_succeeded

    loaded = ContextManager.create().get(ctx.execution_id)

    assert loaded.events
    for event in loaded.events:
        assert event.time.tzinfo is not None
        assert event.time.utcoffset() == timedelta(0)

    # Freshly stamped vs read back: this comparison raises TypeError the moment
    # one side is naive.
    assert loaded.events[-1].time < datetime.now(timezone.utc)


def test_wire_ingest_normalizes_a_naive_checkpoint_to_utc():
    """An older worker checkpoints naive times; the server must not store a
    value that cannot be compared against its own aware stamps."""
    wire = WireExecutionEvent(
        id="abc123",
        type="WORKFLOW_STARTED",
        source_id="src",
        name="wf",
        time="2026-08-07T00:57:11",
    )

    assert wire.time == datetime(2026, 8, 7, 0, 57, 11, tzinfo=timezone.utc)


def test_wire_ingest_converts_an_offset_checkpoint_to_utc():
    wire = WireExecutionEvent(
        id="abc123",
        type="WORKFLOW_STARTED",
        source_id="src",
        name="wf",
        time="2026-08-06T20:57:11-04:00",
    )

    assert wire.time == datetime(2026, 8, 7, 0, 57, 11, tzinfo=timezone.utc)


def test_context_from_json_rebuilds_aware_times():
    """ExecutionContext.from_json feeds ExecutionEvent the isoformat string
    straight from the wire — the worker side of every dispatch."""
    from flux.domain.execution_context import ExecutionContext

    original = ExecutionContext(
        workflow_id="wf-1",
        workflow_namespace="default",
        workflow_name="wf",
        input=None,
        execution_id="exec-1",
        events=[
            ExecutionEvent(
                type=ExecutionEventType.WORKFLOW_STARTED,
                source_id="src",
                name="wf",
                time=datetime(2026, 8, 7, 0, 57, 11, tzinfo=timezone.utc),
            ),
        ],
    )

    rebuilt = ExecutionContext.from_json(original.to_dict())

    assert rebuilt.events[0].time == datetime(2026, 8, 7, 0, 57, 11, tzinfo=timezone.utc)
