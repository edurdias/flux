"""EventHub -- the fan-out layer both console renderers subscribe to.

``ConsoleService`` (flux/agents/console/service.py) is the single server
client; a console process can have many renderers watching the same session
(the web SSE pump, the TUI queue -- both later tasks) that must each see
every event, not compete for one. ``EventHub`` sits between the two: it
drives one ``ConsoleService.send`` stream per turn and fans the parsed
events out to every subscriber's own queue.

The design's core loss-tolerance rule: the persisted execution log is the
source of truth, live SSE is only an overlay. So every turn -- whether it
streamed cleanly or died partway through -- ends with a ``log_delta`` event
carrying a fresh ``get_detail`` read, and subscribers reconcile their view
against that rather than trusting the stream alone. A mid-stream error is
reported (as an ``error`` event) but never raises out of ``run_turn``: doing
so would skip the reconciliation and leave subscribers stuck on a stale
log.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass

from flux.agents.console.service import ConsoleService
from flux.agents.console.titles import derived_title
from flux.agents.events import KIND_ERROR, AgentEvent, parse_event

logger = logging.getLogger(__name__)

# Console-only event kind: the turn-boundary log reconciliation. Deliberately
# not added to flux/agents/events.py's KIND_* constants -- that module is the
# frozen wire-format vocabulary shared with the SSE parser; this one never
# comes off the wire, it's synthesized here from a get_detail() read.
KIND_LOG_DELTA = "log_delta"

# One turn's worth of frames is tens of events; this holds several turns of
# backlog for a briefly-slow renderer while still bounding a dead one.
_SUBSCRIBER_QUEUE_MAX = 512


@dataclass(frozen=True)
class ConsoleEvent:
    """An ``AgentEvent`` addressed to a session, as delivered to subscribers.

    The envelope exists because one hub multiplexes every session a console
    process is watching over shared subscriber queues -- without the
    ``session_id`` a renderer juggling several open sessions couldn't tell
    which one an event belongs to.
    """

    session_id: str
    event: AgentEvent


class EventHub:
    """Fan-out layer: one ``ConsoleService`` feed, many subscriber queues.

    Every subscriber receives every event (fan-out, not work-sharing) --
    each renderer owns its own queue and reads at its own pace.
    """

    def __init__(self, service: ConsoleService) -> None:
        self.service = service
        self._subscribers: list[asyncio.Queue[ConsoleEvent]] = []
        # Sessions with a turn in flight. A set, not a lock: the check has to
        # be answerable synchronously (the /send route decides on a 409
        # before it opens a stream) and a turn is owned by whoever started
        # it, never awaited by the next caller.
        self._turns_in_flight: set[str] = set()
        self.dropped_events = 0
        self._warned_dropping = False
        # session_id -> derived title, fed by open_session/run_turn's detail
        # fetches. Task 6's list endpoints read this so they never need a
        # per-row detail fetch just to show a title.
        self.titles: dict[str, str] = {}

    def subscribe(self) -> asyncio.Queue[ConsoleEvent]:
        """A bounded queue for one renderer.

        Bounded because the hub is a pure publisher: nothing but the
        renderer itself ever removes an item, so a tab that crashed or a
        task that wedged mid-drain grows its queue for the life of the
        process (#245).
        """
        queue: asyncio.Queue[ConsoleEvent] = asyncio.Queue(maxsize=_SUBSCRIBER_QUEUE_MAX)
        self._subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[ConsoleEvent]) -> None:
        try:
            self._subscribers.remove(queue)
        except ValueError:
            pass

    def _publish(self, session_id: str, event: AgentEvent) -> None:
        envelope = ConsoleEvent(session_id=session_id, event=event)
        for queue in self._subscribers:
            # A lagging subscriber loses its oldest events rather than
            # stalling the publisher or every other renderer. Safe because
            # the stream is an overlay: each turn ends with a log_delta
            # carrying freshly read detail, so a subscriber that dropped
            # frames is reconciled at the turn boundary anyway.
            if queue.full():
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
                self.dropped_events += 1
                if not self._warned_dropping:
                    self._warned_dropping = True
                    logger.warning(
                        "console fan-out is dropping events: a subscriber is not "
                        "draining its queue (bound: %s)",
                        _SUBSCRIBER_QUEUE_MAX,
                    )
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(envelope)

    def turn_in_flight(self, session_id: str) -> bool:
        """Whether a turn is already running for this session.

        Read by the /send route to answer 409 before opening a stream;
        ``run_turn`` re-checks it, so this is a nicer status code rather
        than the guard itself.
        """
        return session_id in self._turns_in_flight

    def _cache_title(self, session_id: str, detail: dict) -> None:
        title = derived_title(detail)
        if title is not None:
            self.titles[session_id] = title

    async def run_turn(
        self,
        session_id: str,
        agent_name: str,
        workflow_name: str,
        text: str,
    ) -> None:
        """Drive one turn of ``session_id`` and fan its events out.

        Runs every raw SSE frame from ``service.send`` through
        ``parse_event`` (one frame can yield several ``AgentEvent``s) and
        publishes each, enveloped with ``session_id``. Whether the stream
        ends cleanly or raises partway through, the turn always closes with
        exactly one ``log_delta`` event carrying a fresh ``get_detail`` read
        -- the turn-boundary reconciliation subscribers rely on to catch up
        with what actually got persisted.

        One call does not close that way: a turn refused because this
        session already has one in flight never starts, and publishes an
        error event *only*. A ``log_delta`` there would terminate the live
        turn's own SSE stream, which ends at the first one it sees for its
        session -- so "exactly one log_delta" is a guarantee about a turn
        that ran, not about every call.

        The reconciliation read can fail too -- often the very same
        server/network hiccup that broke the stream also breaks the
        follow-up ``get_detail`` call. That failure is degraded, never
        raised: subscribers still get exactly one ``log_delta``, shaped as
        ``{"detail": None, "error": <reason>}`` so a renderer can tell
        "fresh detail" apart from "reconciliation failed, keep whatever you
        last had" instead of getting no event -- or an unhandled exception
        -- for the turn. An ``error`` event precedes it unless the stream
        already emitted one for this turn (no point doubling up).
        """
        if session_id in self._turns_in_flight:
            # Two turns for one session interleave their frames into every
            # subscriber and race the same execution; the server's
            # non-PAUSED rejection only catches it after the second turn is
            # already streaming. Reported as an error event and nothing
            # else -- a log_delta here would end the *live* turn's own SSE
            # stream, which stops at the first log_delta for its session.
            message = f"A turn is already running for session {session_id}"
            logger.warning("run_turn: %s", message)
            self._publish(session_id, AgentEvent(kind=KIND_ERROR, data={"message": message}))
            return

        self._turns_in_flight.add(session_id)
        try:
            await self._run_turn_body(session_id, agent_name, workflow_name, text)
        finally:
            # Its own finally, not a line after the reconciliation read: a
            # cancellation delivered during that read is not an Exception
            # and leaves the block without running anything after it, which
            # would wedge the session for the life of the process.
            self._turns_in_flight.discard(session_id)

    async def _run_turn_body(
        self,
        session_id: str,
        agent_name: str,
        workflow_name: str,
        text: str,
    ) -> None:
        """``run_turn`` minus the one-turn-per-session bookkeeping."""
        stream_failed = False
        try:
            async for raw in self.service.send(session_id, agent_name, workflow_name, text):
                for event in parse_event(raw):
                    self._publish(session_id, event)
        except Exception as exc:
            # Loss-tolerant by design: the stream is only an overlay, so a
            # broken connection is reported to subscribers as data, not
            # raised -- the log_delta below still runs and catches them up.
            stream_failed = True
            logger.warning(
                "run_turn: stream failed for session %s",
                session_id,
                exc_info=True,
            )
            self._publish(session_id, AgentEvent(kind=KIND_ERROR, data={"message": str(exc)}))
        finally:
            try:
                detail = await self.service.get_detail(session_id)
            except Exception as exc:
                logger.warning(
                    "run_turn: reconciliation get_detail failed for session %s",
                    session_id,
                    exc_info=True,
                )
                if not stream_failed:
                    self._publish(
                        session_id,
                        AgentEvent(kind=KIND_ERROR, data={"message": str(exc)}),
                    )
                self._publish(
                    session_id,
                    AgentEvent(
                        kind=KIND_LOG_DELTA,
                        data={"detail": None, "error": str(exc)},
                    ),
                )
            else:
                self._cache_title(session_id, detail)
                self._publish(
                    session_id,
                    AgentEvent(kind=KIND_LOG_DELTA, data={"detail": detail}),
                )

    async def open_session(self, session_id: str) -> dict:
        """Fetch a session's current detail for initial render, and cache its title."""
        detail = await self.service.get_detail(session_id)
        self._cache_title(session_id, detail)
        return detail
