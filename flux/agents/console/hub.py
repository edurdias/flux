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
        # session_id -> derived title, fed by open_session/run_turn's detail
        # fetches. Task 6's list endpoints read this so they never need a
        # per-row detail fetch just to show a title.
        self.titles: dict[str, str] = {}

    def subscribe(self) -> asyncio.Queue[ConsoleEvent]:
        queue: asyncio.Queue[ConsoleEvent] = asyncio.Queue()
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
            queue.put_nowait(envelope)

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
