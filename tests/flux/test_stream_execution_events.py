"""The run/stream SSE loop must advance on log growth, not on wall clock.

Gating frames on ``events[-1].time >`` wedged the stream permanently whenever
a later event carried an earlier timestamp: the hydration ordinal had already
moved on, so the context was never refreshed again and the loop spun until the
client gave up (#169). Timezone skew was one way to produce that; an NTP step
or a mid-upgrade worker are others.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from datetime import timedelta
from datetime import timezone

import pytest
from typing import Literal

from flux.execution_signals import ExecutionSignals
from flux.domain.events import ExecutionEvent
from flux.domain.events import ExecutionEventType
from flux.domain.events import ExecutionState
from flux.domain.execution_context import ExecutionContext
from flux.server import Server


class _TickingEvent(asyncio.Event):
    """Always ready, never latches — drives the loop without a real worker."""

    async def wait(self) -> Literal[True]:
        await asyncio.sleep(0.01)
        return True

    def clear(self) -> None:
        pass


def _signals_with(event, buffer=None) -> ExecutionSignals:
    """An ExecutionSignals pre-seeded with the fakes this test drives.

    The event has to be the _TickingEvent (nothing else advances the loop),
    and ExecutionSignals deliberately exposes no setter for it, so the
    fixture seeds the state directly.
    """
    signals = ExecutionSignals()
    signals._events["exec-1"] = event
    if buffer is not None:
        signals._progress_buffers["exec-1"] = buffer
    return signals


class _FakeManager:
    """Serves one hydration: the execution advances to COMPLETED exactly once."""

    def __init__(self, completed: ExecutionContext):
        self._completed = completed

    def last_event_ordinal(self, execution_id: str) -> int:
        return 2

    def get(self, execution_id: str) -> ExecutionContext:
        return self._completed


def _ctx(state: ExecutionState, events: list[ExecutionEvent]) -> ExecutionContext:
    return ExecutionContext(
        workflow_id="wf-1",
        workflow_namespace="default",
        workflow_name="probe",
        input=None,
        execution_id="exec-1",
        state=state,
        events=events,
    )


def _event(type: ExecutionEventType, time: datetime) -> ExecutionEvent:
    return ExecutionEvent(type=type, source_id="exec-1", name="probe", time=time)


@pytest.mark.asyncio
async def test_stream_emits_terminal_frame_when_the_clock_moves_backwards():
    later = datetime(2026, 8, 7, 0, 57, 11, tzinfo=timezone.utc)
    earlier = later - timedelta(hours=4)

    claimed = _ctx(
        ExecutionState.CLAIMED,
        [_event(ExecutionEventType.WORKFLOW_CLAIMED, later)],
    )
    completed = _ctx(
        ExecutionState.COMPLETED,
        [
            _event(ExecutionEventType.WORKFLOW_CLAIMED, later),
            _event(ExecutionEventType.WORKFLOW_COMPLETED, earlier),
        ],
    )
    assert completed.has_finished

    server = Server.__new__(Server)
    server.signals = _signals_with(_TickingEvent())

    frames = []

    async def collect():
        async for frame in server._stream_execution_events(
            claimed,
            _FakeManager(completed),
            detailed=False,
        ):
            frames.append(frame)

    await asyncio.wait_for(collect(), timeout=5.0)

    assert [f["event"] for f in frames] == ["probe.execution.completed"]


@pytest.mark.asyncio
async def test_stream_does_not_re_emit_an_unchanged_log():
    """The initial frame must not be duplicated when hydration finds no new
    events — the property the time comparison was providing."""
    stamp = datetime(2026, 8, 7, 0, 57, 11, tzinfo=timezone.utc)
    events = [
        _event(ExecutionEventType.WORKFLOW_CLAIMED, stamp),
        _event(ExecutionEventType.WORKFLOW_COMPLETED, stamp + timedelta(seconds=1)),
    ]
    finished = _ctx(ExecutionState.COMPLETED, events)

    server = Server.__new__(Server)
    server.signals = _signals_with(_TickingEvent())

    frames = []

    async def collect():
        async for frame in server._stream_execution_events(
            finished,
            _FakeManager(finished),
            detailed=False,
            emit_initial=True,
        ):
            frames.append(frame)

    await asyncio.wait_for(collect(), timeout=5.0)

    assert [f["event"] for f in frames] == ["probe.execution.completed"]
