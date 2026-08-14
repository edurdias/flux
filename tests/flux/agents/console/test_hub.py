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

    def __init__(self, frames, detail, error_after: int | None = None):
        super().__init__(server_url="http://test", token=None)
        self._frames = frames
        self._detail = detail
        self._error_after = error_after
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
