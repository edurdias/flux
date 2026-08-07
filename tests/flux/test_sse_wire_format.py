"""SSE payloads go on the wire as one JSON document per ``data:`` line.

sse-starlette splits a payload on newlines, so pretty-printed JSON shipped
every event as a run of consecutive ``data:`` lines. That is legal SSE, but
line-based consumers — the de-facto convention for JSON-over-SSE — parse zero
events from it, silently, and the indentation is paid per progress frame
(#168).
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from datetime import timezone

import pytest
from typing import Literal

from flux.api.schemas import _inject_trace_context
from flux.domain.events import ExecutionEvent
from flux.domain.events import ExecutionEventType
from flux.domain.events import ExecutionState
from flux.domain.execution_context import ExecutionContext
from flux.server import Server
from flux.utils import to_json
from flux.utils import to_wire_json

_PAYLOAD = {
    "workflow_name": "probe",
    "input": {"n": 24},
    "output": None,
    "state": "CLAIMED",
    "nested": {"deep": [1, 2, {"deeper": True}]},
}


def test_to_wire_json_is_a_single_line():
    assert "\n" not in to_wire_json(_PAYLOAD)


def test_to_wire_json_has_no_padding_between_tokens():
    wire = to_wire_json(_PAYLOAD)
    assert '"state":"CLAIMED"' in wire
    assert ", " not in wire


def test_to_wire_json_parses_to_the_same_object_as_the_pretty_form():
    """Backward compatible: a spec-compliant client sees an identical object."""
    assert json.loads(to_wire_json(_PAYLOAD)) == json.loads(to_json(_PAYLOAD))


def test_to_wire_json_escapes_newlines_inside_strings():
    """Compact JSON cannot contain a literal newline, so payload == one line
    by construction and the SSE splitting path is never exercised."""
    wire = to_wire_json({"text": "line one\nline two"})

    assert "\n" not in wire
    assert json.loads(wire)["text"] == "line one\nline two"


def test_to_wire_json_serializes_flux_types():
    wire = to_wire_json({"at": datetime(2026, 8, 7, tzinfo=timezone.utc)})

    assert "\n" not in wire
    assert json.loads(wire)["at"] == "2026-08-07T00:00:00+00:00"


def test_to_json_stays_pretty_for_human_surfaces():
    """The CLI and debug logs keep the indented form."""
    assert "\n" in to_json(_PAYLOAD)


def test_inject_trace_context_keeps_the_payload_on_one_line(monkeypatch):
    monkeypatch.setattr("flux.observability.is_enabled", lambda: True)
    monkeypatch.setattr(
        "flux.observability.tracing.inject_trace_context",
        lambda: {"traceparent": "00-abc-def-01"},
    )

    result = _inject_trace_context(to_wire_json(_PAYLOAD))

    assert "\n" not in result
    assert json.loads(result)["trace_context"] == {"traceparent": "00-abc-def-01"}


class _TickingEvent(asyncio.Event):
    async def wait(self) -> Literal[True]:
        await asyncio.sleep(0.01)
        return True

    def clear(self) -> None:
        pass


class _FakeManager:
    def __init__(self, ctx):
        self._ctx = ctx

    def last_event_ordinal(self, execution_id: str) -> int:
        return 2

    def get(self, execution_id: str):
        return self._ctx


@pytest.mark.asyncio
async def test_execution_stream_frames_are_single_line():
    stamp = datetime(2026, 8, 7, 0, 57, 11, tzinfo=timezone.utc)
    ctx = ExecutionContext(
        workflow_id="wf-1",
        workflow_namespace="default",
        workflow_name="probe",
        input={"n": 24},
        execution_id="exec-1",
        state=ExecutionState.COMPLETED,
        events=[
            ExecutionEvent(
                type=ExecutionEventType.WORKFLOW_COMPLETED,
                source_id="exec-1",
                name="probe",
                time=stamp,
            ),
        ],
    )

    server = Server.__new__(Server)
    server._execution_events = {"exec-1": _TickingEvent()}
    server._progress_buffers = {}

    frames = []

    async def collect():
        async for frame in server._stream_execution_events(
            ctx,
            _FakeManager(ctx),
            detailed=True,
            emit_initial=True,
        ):
            frames.append(frame)

    await asyncio.wait_for(collect(), timeout=5.0)

    assert frames
    for frame in frames:
        assert "\n" not in frame["data"], f"multi-line SSE data in {frame['event']}"
        json.loads(frame["data"])


@pytest.mark.asyncio
async def test_progress_frames_are_single_line():
    """Progress is the highest-frequency frame the server emits."""
    stamp = datetime(2026, 8, 7, 0, 57, 11, tzinfo=timezone.utc)
    ctx = ExecutionContext(
        workflow_id="wf-1",
        workflow_namespace="default",
        workflow_name="probe",
        input=None,
        execution_id="exec-1",
        state=ExecutionState.RUNNING,
        events=[
            ExecutionEvent(
                type=ExecutionEventType.WORKFLOW_STARTED,
                source_id="exec-1",
                name="probe",
                time=stamp,
            ),
        ],
    )

    buffer: asyncio.Queue = asyncio.Queue()
    buffer.put_nowait(
        ExecutionEvent(
            type=ExecutionEventType.TASK_PROGRESS,
            source_id="task-1",
            name="step",
            value={"pct": 50, "note": "halfway"},
            time=stamp,
        ),
    )

    server = Server.__new__(Server)
    server._execution_events = {"exec-1": _TickingEvent()}
    server._progress_buffers = {"exec-1": buffer}

    frames = []

    async def collect():
        async for frame in server._stream_execution_events(
            ctx,
            _FakeManager(ctx),
            detailed=False,
        ):
            frames.append(frame)
            if len(frames) >= 1:
                break

    await asyncio.wait_for(collect(), timeout=5.0)

    assert [f["event"] for f in frames] == ["task.progress"]
    assert "\n" not in frames[0]["data"]
    assert json.loads(frames[0]["data"])["value"] == {"pct": 50, "note": "halfway"}
