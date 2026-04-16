"""Tests for agent event parsing."""

from __future__ import annotations

from flux.agents.events import AgentEvent, parse_event


def test_parses_session_id_event():
    raw = {"execution_id": "exec-123"}
    events = list(parse_event(raw))
    assert events == [AgentEvent(kind="session_id", data={"id": "exec-123"})]


def test_parses_token_progress():
    raw = {"type": "task.progress", "value": {"token": "hello"}}
    events = list(parse_event(raw))
    assert events == [AgentEvent(kind="token", data={"text": "hello"})]


def test_parses_tool_start():
    raw = {
        "type": "task.progress",
        "value": {"type": "tool_start", "name": "shell", "args": {"cmd": "ls"}},
    }
    events = list(parse_event(raw))
    assert events == [
        AgentEvent(kind="tool_start", data={"name": "shell", "args": {"cmd": "ls"}}),
    ]


def test_parses_tool_done():
    raw = {
        "type": "task.progress",
        "value": {"type": "tool_done", "name": "shell", "status": "success"},
    }
    events = list(parse_event(raw))
    assert events == [
        AgentEvent(kind="tool_done", data={"name": "shell", "status": "success"}),
    ]


def test_parses_chat_response_pause():
    raw = {
        "type": "execution.paused",
        "output": {"type": "chat_response", "content": "hi", "turn": 1},
    }
    events = list(parse_event(raw))
    assert events == [
        AgentEvent(kind="chat_response", data={"content": "hi", "turn": 1}),
    ]


def test_parses_elicitation_pause():
    raw = {
        "type": "execution.paused",
        "output": {
            "type": "elicitation",
            "mode": "url",
            "elicitation_id": "el-1",
            "url": "https://auth.example.com",
            "message": "Authorize",
            "server_name": "github",
        },
    }
    events = list(parse_event(raw))
    assert len(events) == 1
    assert events[0].kind == "elicitation"
    assert events[0].data["elicitation_id"] == "el-1"
    assert events[0].data["url"] == "https://auth.example.com"


def test_parses_session_end_pause():
    raw = {
        "type": "execution.paused",
        "output": {"type": "session_end", "reason": "user_exit", "turns": 3},
    }
    events = list(parse_event(raw))
    assert events == [
        AgentEvent(kind="session_end", data={"reason": "user_exit", "turns": 3}),
    ]


def test_ignores_unknown_events():
    raw = {"type": "something.unrelated", "foo": "bar"}
    events = list(parse_event(raw))
    assert events == []


def test_parses_execution_id_alongside_other_data():
    raw = {
        "execution_id": "exec-1",
        "type": "task.progress",
        "value": {"token": "ok"},
    }
    events = list(parse_event(raw))
    assert AgentEvent(kind="session_id", data={"id": "exec-1"}) in events
    assert AgentEvent(kind="token", data={"text": "ok"}) in events


def test_parses_task_progress_with_null_value():
    raw = {"type": "task.progress", "value": None}
    events = list(parse_event(raw))
    assert events == []


def test_parses_paused_with_null_output():
    raw = {"type": "execution.paused", "output": None}
    events = list(parse_event(raw))
    assert events == []
