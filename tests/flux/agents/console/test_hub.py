"""Tests for EventHub -- session-enveloped fan-out with turn-boundary log reconciliation."""

from __future__ import annotations

import asyncio

import pytest

from flux.agents.console.hub import KIND_LOG_DELTA, ConsoleEvent, EventHub
from flux.agents.console.service import ConsoleService

TOKEN_FRAME = {"type": "TASK_PROGRESS", "value": {"token": "hi"}}
PAUSED_FRAME = {
    "execution_id": "exec-1",
    "state": "PAUSED",
    "output": {"type": "chat_response", "content": "hello back", "turn": 1},
}
DETAIL = {"execution_id": "exec-1", "events": []}


class _FakeService(ConsoleService):
    """A ConsoleService stand-in that streams canned frames instead of hitting HTTP.

    Subclasses the real service (rather than a bare duck-typed double) so the
    hub is exercised against the declared ``ConsoleService`` contract.
    """

    def __init__(
        self,
        frames,
        detail,
        error_after: int | None = None,
        detail_raises: bool = False,
    ):
        super().__init__(server_url="http://test", token=None)
        self._frames = frames
        self._detail = detail
        self._error_after = error_after
        self._detail_raises = detail_raises
        self.detail_calls = 0
        self.detail_requested_for: list[str] = []

    async def send(self, execution_id, agent_name, workflow_name, text):
        for i, frame in enumerate(self._frames):
            if self._error_after is not None and i == self._error_after:
                raise RuntimeError("stream broke")
            yield frame

    async def get_detail(self, execution_id):
        self.detail_calls += 1
        self.detail_requested_for.append(execution_id)
        if self._detail_raises:
            raise RuntimeError("get_detail exploded")
        return self._detail


async def _drain(queue: asyncio.Queue[ConsoleEvent]) -> list[ConsoleEvent]:
    items = []
    while not queue.empty():
        items.append(queue.get_nowait())
    return items


@pytest.mark.asyncio
async def test_run_turn_fans_out_every_event_to_every_subscriber_with_session_id():
    service = _FakeService([TOKEN_FRAME, PAUSED_FRAME], DETAIL)
    hub = EventHub(service)
    sub_a = hub.subscribe()
    sub_b = hub.subscribe()

    await hub.run_turn("exec-1", "coder", "agent_chat", "hi there")

    events_a = await _drain(sub_a)
    events_b = await _drain(sub_b)

    assert [e.event.kind for e in events_a] == [
        "token",
        "session_id",
        "chat_response",
        "log_delta",
    ]
    assert events_a == events_b
    assert all(e.session_id == "exec-1" for e in events_a)


@pytest.mark.asyncio
async def test_run_turn_emits_exactly_one_log_delta_with_fresh_detail():
    service = _FakeService([TOKEN_FRAME], DETAIL)
    hub = EventHub(service)
    sub = hub.subscribe()

    await hub.run_turn("exec-1", "coder", "agent_chat", "hi")

    events = await _drain(sub)
    log_deltas = [e for e in events if e.event.kind == KIND_LOG_DELTA]
    assert len(log_deltas) == 1
    assert log_deltas[0].event.data == {"detail": DETAIL}
    assert log_deltas[0].session_id == "exec-1"
    assert service.detail_calls == 1
    assert service.detail_requested_for == ["exec-1"]


@pytest.mark.asyncio
async def test_run_turn_mid_stream_exception_emits_error_then_log_delta_and_does_not_raise():
    service = _FakeService([TOKEN_FRAME, PAUSED_FRAME], DETAIL, error_after=1)
    hub = EventHub(service)
    sub = hub.subscribe()

    # Loss-tolerance contract: a stream error must not propagate to callers.
    await hub.run_turn("exec-1", "coder", "agent_chat", "hi")

    events = await _drain(sub)
    kinds = [e.event.kind for e in events]
    assert kinds == ["token", "error", "log_delta"]
    assert service.detail_calls == 1


@pytest.mark.asyncio
async def test_run_turn_reconciliation_failure_after_clean_stream_degrades_not_raises():
    """Stream succeeds, but the follow-up get_detail explodes -- the same
    server/network hiccup that could break a stream can just as easily break
    the reconciliation call right after it. Subscribers must still get
    exactly one log_delta (degraded), not zero events and not an exception
    out of run_turn."""
    service = _FakeService([TOKEN_FRAME], DETAIL, detail_raises=True)
    hub = EventHub(service)
    sub = hub.subscribe()

    await hub.run_turn("exec-1", "coder", "agent_chat", "hi")

    events = await _drain(sub)
    kinds = [e.event.kind for e in events]
    assert kinds == ["token", "error", "log_delta"]
    log_delta = events[-1].event
    assert log_delta.data["detail"] is None
    assert "get_detail exploded" in log_delta.data["error"]
    assert service.detail_calls == 1


@pytest.mark.asyncio
async def test_run_turn_stream_and_reconciliation_both_fail_emits_one_error_and_degraded_log_delta():
    """When the stream itself failed, its error event already covers the
    turn -- reconciliation failing too must not double up on error events,
    but must still close with a degraded log_delta rather than raising."""
    service = _FakeService([TOKEN_FRAME, PAUSED_FRAME], DETAIL, error_after=1, detail_raises=True)
    hub = EventHub(service)
    sub = hub.subscribe()

    await hub.run_turn("exec-1", "coder", "agent_chat", "hi")

    events = await _drain(sub)
    kinds = [e.event.kind for e in events]
    assert kinds == ["token", "error", "log_delta"]
    log_delta = events[-1].event
    assert log_delta.data["detail"] is None
    assert "get_detail exploded" in log_delta.data["error"]


@pytest.mark.asyncio
async def test_open_session_returns_detail_and_feeds_title_cache():
    detail = {
        "execution_id": "exec-2",
        "events": [{"type": "WORKFLOW_RESUMED", "value": {"message": "Fix the bug please"}}],
    }
    service = _FakeService([], detail)
    hub = EventHub(service)

    result = await hub.open_session("exec-2")

    assert result == detail
    assert hub.titles.get("exec-2") == "Fix the bug please"


@pytest.mark.asyncio
async def test_open_session_does_not_cache_when_no_user_message_yet():
    detail = {"execution_id": "exec-3", "events": []}
    service = _FakeService([], detail)
    hub = EventHub(service)

    await hub.open_session("exec-3")

    assert "exec-3" not in hub.titles


@pytest.mark.asyncio
async def test_run_turn_feeds_title_cache_from_its_own_reconciliation_detail():
    detail = {
        "execution_id": "exec-4",
        "events": [{"type": "WORKFLOW_RESUMED", "value": {"message": "Ship the release"}}],
    }
    service = _FakeService([], detail)
    hub = EventHub(service)

    await hub.run_turn("exec-4", "coder", "agent_chat", "Ship the release")

    assert hub.titles.get("exec-4") == "Ship the release"


@pytest.mark.asyncio
async def test_unsubscribe_stops_future_delivery():
    service = _FakeService([TOKEN_FRAME], DETAIL)
    hub = EventHub(service)
    sub = hub.subscribe()
    hub.unsubscribe(sub)

    await hub.run_turn("exec-1", "coder", "agent_chat", "hi")

    assert sub.empty()


@pytest.mark.asyncio
async def test_subscribe_returns_independent_queues():
    service = _FakeService([], DETAIL)
    hub = EventHub(service)
    sub_a = hub.subscribe()
    sub_b = hub.subscribe()

    assert sub_a is not sub_b


# ---------------------------------------------------------------------------
# Bounded fan-out and one-turn-per-session (#245)
# ---------------------------------------------------------------------------


class _BlockingService(_FakeService):
    """Holds its stream open until released, so a turn can be observed
    mid-flight."""

    def __init__(self, detail):
        super().__init__([], detail)
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.sends = 0

    async def send(self, execution_id, agent_name, workflow_name, text):
        self.sends += 1
        self.started.set()
        await self.release.wait()
        for frame in [TOKEN_FRAME]:
            yield frame


@pytest.mark.asyncio
async def test_a_subscriber_queue_is_bounded():
    """An unbounded queue is a leak with a publisher: a renderer that stops
    draining (crashed tab, wedged task) grows it for the life of the
    process, since nothing else ever removes an item."""
    hub = EventHub(_FakeService([], DETAIL))
    queue = hub.subscribe()

    assert queue.maxsize > 0


@pytest.mark.asyncio
async def test_publishing_past_the_bound_drops_the_oldest_not_the_newest():
    """The stream is an overlay -- every turn ends with a log_delta carrying
    fresh detail -- so a lagging subscriber is better served by the most
    recent events than by a stalled publisher or the first N it never read."""
    hub = EventHub(_FakeService([], DETAIL))
    queue = hub.subscribe()

    from flux.agents.events import AgentEvent

    for index in range(queue.maxsize + 5):
        hub._publish("exec-1", AgentEvent(kind="token", data={"n": index}))

    drained = await _drain(queue)
    assert len(drained) == queue.maxsize
    # The newest event survived; the oldest were the ones dropped.
    assert drained[-1].event.data == {"n": queue.maxsize + 4}
    assert drained[0].event.data == {"n": 5}


@pytest.mark.asyncio
async def test_a_full_subscriber_never_blocks_the_others():
    hub = EventHub(_FakeService([], DETAIL))
    stalled = hub.subscribe()
    live = hub.subscribe()

    from flux.agents.events import AgentEvent

    for index in range(stalled.maxsize + 3):
        hub._publish("exec-1", AgentEvent(kind="token", data={"n": index}))
        # The live subscriber keeps up, so it must see every event.
        live.get_nowait()

    assert live.empty()
    assert stalled.full()


@pytest.mark.asyncio
async def test_a_second_turn_for_a_live_session_is_refused_not_run():
    """Two turns for one session interleave their frames into every
    subscriber and race the same execution. The server's non-PAUSED
    rejection is a backstop, not a guard -- it is reached only after the
    second turn has already started streaming."""
    service = _BlockingService(DETAIL)
    hub = EventHub(service)
    sub = hub.subscribe()

    first = asyncio.create_task(hub.run_turn("exec-1", "coder", "agent_chat", "one"))
    await service.started.wait()
    assert hub.turn_in_flight("exec-1")

    # Never raises, per run_turn's contract -- the refusal is data.
    await hub.run_turn("exec-1", "coder", "agent_chat", "two")

    assert service.sends == 1
    refusal = await _drain(sub)
    # An error and nothing else: a log_delta here would terminate the *live*
    # turn's own SSE stream, which ends on the first log_delta it sees for
    # its session (flux/agents/console/app.py::console_send).
    assert [e.event.kind for e in refusal] == ["error"]
    assert "already running" in refusal[0].event.data["message"]

    service.release.set()
    await first
    assert not hub.turn_in_flight("exec-1")


@pytest.mark.asyncio
async def test_a_turn_for_another_session_runs_alongside():
    """The guard is per session, not a global lock: two sessions are two
    executions and must be able to run at once."""
    service = _BlockingService(DETAIL)
    hub = EventHub(service)

    first = asyncio.create_task(hub.run_turn("exec-1", "coder", "agent_chat", "one"))
    await service.started.wait()
    second = asyncio.create_task(hub.run_turn("exec-2", "coder", "agent_chat", "two"))
    await asyncio.sleep(0)

    service.release.set()
    await asyncio.gather(first, second)

    assert service.sends == 2


@pytest.mark.asyncio
async def test_the_slot_is_released_after_a_failed_turn():
    """A turn that broke mid-stream must not wedge its session forever."""
    service = _FakeService([TOKEN_FRAME], DETAIL, error_after=0)
    hub = EventHub(service)

    await hub.run_turn("exec-1", "coder", "agent_chat", "hi")

    assert not hub.turn_in_flight("exec-1")


@pytest.mark.asyncio
async def test_the_slot_is_released_when_a_turn_is_cancelled():
    """A cancelled turn (shutdown, a future timeout wrapper) must not wedge
    its session: the release cannot live after an await that cancellation
    re-raises through."""
    service = _BlockingService(DETAIL)
    hub = EventHub(service)

    task = asyncio.create_task(hub.run_turn("exec-1", "coder", "agent_chat", "hi"))
    await service.started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert not hub.turn_in_flight("exec-1")


@pytest.mark.asyncio
async def test_the_slot_is_released_when_reconciliation_itself_is_cancelled():
    """Cancellation delivered while the turn-boundary read is in flight
    escapes the reconciliation block entirely (CancelledError is not an
    Exception), so a release written after that read never runs."""

    class _CancelDuringDetail(_FakeService):
        async def get_detail(self, execution_id):
            raise asyncio.CancelledError()

    hub = EventHub(_CancelDuringDetail([TOKEN_FRAME], DETAIL))

    with pytest.raises(asyncio.CancelledError):
        await hub.run_turn("exec-1", "coder", "agent_chat", "hi")

    assert not hub.turn_in_flight("exec-1")
