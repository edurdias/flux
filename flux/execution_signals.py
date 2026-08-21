"""Per-execution signalling: waiters, progress buffers, queue timing.

Extracted from ``flux.server`` (#264 stage 3). Three dictionaries used to
live on ``Server`` and were poked directly from four route modules -- 27
call sites of ``setdefault``/``pop``/``get``/subscript, each one a little
piece of the same protocol written out longhand.

The protocol, named:

- an execution has an **event** that request handlers wait on and writers
  set, created when someone starts waiting and dropped when nobody will;
- a *streaming* execution has a **progress buffer**, a bounded queue that
  drops rather than grows (progress frames are an overlay, not a record);
- an execution created but not yet claimed has a **queued-at stamp**, read
  once when it starts to report how long dispatch made it wait.

Owning the dictionaries here is the point: the state that three route
modules and the scheduler tick coordinate through now has one definition
and one vocabulary, instead of being a convention spread across files.
"""

from __future__ import annotations

import asyncio

PROGRESS_BUFFER_MAX = 10000


class ExecutionSignals:
    """The signalling state for in-flight executions."""

    def __init__(self) -> None:
        self._events: dict[str, asyncio.Event] = {}
        self._progress_buffers: dict[str, asyncio.Queue] = {}
        self._queued_at: dict[str, float] = {}

    # -- waiters ---------------------------------------------------------

    def event_for(self, execution_id: str) -> asyncio.Event:
        """The execution's event, created if this is the first waiter."""
        return self._events.setdefault(execution_id, asyncio.Event())

    def event(self, execution_id: str) -> asyncio.Event | None:
        """The execution's event if one exists -- writers signal through it,
        and a write with nobody waiting is not an error."""
        return self._events.get(execution_id)

    def notify(self, execution_id: str) -> None:
        """Wake anyone waiting on this execution, if anyone is."""
        event = self._events.get(execution_id)
        if event is not None:
            event.set()

    def drop_event(self, execution_id: str) -> None:
        self._events.pop(execution_id, None)

    # -- progress buffers ------------------------------------------------

    def open_progress_buffer(self, execution_id: str) -> asyncio.Queue:
        """A bounded buffer for one streaming consumer.

        Bounded because progress frames are an overlay on the persisted
        log: dropping the oldest under pressure loses a frame the consumer
        can re-derive, while growing without limit loses the process.
        """
        buffer: asyncio.Queue = asyncio.Queue(maxsize=PROGRESS_BUFFER_MAX)
        self._progress_buffers[execution_id] = buffer
        return buffer

    def progress_buffer(self, execution_id: str) -> asyncio.Queue | None:
        return self._progress_buffers.get(execution_id)

    def drop_progress_buffer(self, execution_id: str) -> asyncio.Queue | None:
        return self._progress_buffers.pop(execution_id, None)

    # -- queue timing ----------------------------------------------------

    def stamp_queued(self, execution_id: str, at: float) -> None:
        self._queued_at[execution_id] = at

    def take_queued_at(self, execution_id: str) -> float | None:
        """Read and forget: the wait is reported once, when work starts."""
        return self._queued_at.pop(execution_id, None)

    def forget(self, execution_id: str) -> None:
        """Drop everything for a finished execution."""
        self._events.pop(execution_id, None)
        self._progress_buffers.pop(execution_id, None)
        self._queued_at.pop(execution_id, None)
