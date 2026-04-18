"""Tests for agent event parsing."""

from __future__ import annotations

from flux.agents.events import AgentEvent, is_terminal_state, parse_event


def test_parses_session_id_event():
    raw = {"execution_id": "exec-123"}
    events = list(parse_event(raw))
    assert events == [AgentEvent(kind="session_id", data={"id": "exec-123"})]


def test_parses_token_progress():
    raw = {"type": "TASK_PROGRESS", "value": {"token": "hello"}}
    events = list(parse_event(raw))
    assert events == [AgentEvent(kind="token", data={"text": "hello"})]


def test_parses_tool_start():
    raw = {
        "type": "TASK_PROGRESS",
        "value": {"type": "tool_start", "name": "shell", "args": {"cmd": "ls"}},
    }
    events = list(parse_event(raw))
    assert events == [
        AgentEvent(kind="tool_start", data={"name": "shell", "args": {"cmd": "ls"}}),
    ]


def test_parses_tool_done():
    raw = {
        "type": "TASK_PROGRESS",
        "value": {"type": "tool_done", "name": "shell", "status": "success"},
    }
    events = list(parse_event(raw))
    assert events == [
        AgentEvent(kind="tool_done", data={"name": "shell", "status": "success"}),
    ]


def test_parses_chat_response_pause_from_state_dto():
    """Real Flux DTO shape: state=PAUSED + output dict, no 'type' key."""
    raw = {
        "execution_id": "exec-1",
        "state": "PAUSED",
        "output": {"type": "chat_response", "content": "hi", "turn": 1},
    }
    events = list(parse_event(raw))
    kinds = [e.kind for e in events]
    assert "session_id" in kinds
    chat_events = [e for e in events if e.kind == "chat_response"]
    assert chat_events == [
        AgentEvent(kind="chat_response", data={"content": "hi", "turn": 1}),
    ]


def test_parses_elicitation_pause_from_state_dto():
    raw = {
        "state": "PAUSED",
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


def test_parses_session_end_pause_from_state_dto():
    raw = {
        "state": "PAUSED",
        "output": {"type": "session_end", "reason": "user_exit", "turns": 3},
    }
    events = list(parse_event(raw))
    assert events == [
        AgentEvent(kind="session_end", data={"reason": "user_exit", "turns": 3}),
    ]


def test_lowercase_state_is_accepted():
    """Defensive: server may emit lowercase state values in some code paths."""
    raw = {
        "state": "paused",
        "output": {"type": "chat_response", "content": "ok", "turn": 0},
    }
    events = list(parse_event(raw))
    assert any(e.kind == "chat_response" for e in events)


def test_ignores_unknown_events():
    raw = {"type": "something.unrelated", "foo": "bar"}
    events = list(parse_event(raw))
    assert events == []


def test_ignores_non_terminal_state_frame():
    """A state frame with RUNNING/CLAIMED has no agent-visible content."""
    raw = {"execution_id": "exec-1", "state": "RUNNING", "output": None}
    events = list(parse_event(raw))
    # Only the session_id handshake is meaningful.
    assert events == [AgentEvent(kind="session_id", data={"id": "exec-1"})]


def test_parses_execution_id_alongside_task_progress():
    raw = {
        "execution_id": "exec-1",
        "type": "TASK_PROGRESS",
        "value": {"token": "ok"},
    }
    events = list(parse_event(raw))
    assert AgentEvent(kind="session_id", data={"id": "exec-1"}) in events
    assert AgentEvent(kind="token", data={"text": "ok"}) in events


def test_parses_task_progress_with_null_value():
    raw = {"type": "TASK_PROGRESS", "value": None}
    events = list(parse_event(raw))
    assert events == []


def test_parses_paused_with_null_output():
    raw = {"state": "PAUSED", "output": None}
    events = list(parse_event(raw))
    assert events == []


# --- is_terminal_state ----------------------------------------------------


def test_is_terminal_state_paused():
    assert is_terminal_state({"state": "PAUSED"}) is True


def test_is_terminal_state_completed():
    assert is_terminal_state({"state": "COMPLETED"}) is True


def test_is_terminal_state_failed():
    assert is_terminal_state({"state": "FAILED"}) is True


def test_is_terminal_state_cancelled():
    assert is_terminal_state({"state": "CANCELLED"}) is True


def test_is_terminal_state_running():
    assert is_terminal_state({"state": "RUNNING"}) is False


def test_is_terminal_state_no_state():
    assert is_terminal_state({"type": "TASK_PROGRESS"}) is False


def test_is_terminal_state_lowercase():
    assert is_terminal_state({"state": "paused"}) is True


def test_parses_reasoning_progress():
    raw = {
        "type": "TASK_PROGRESS",
        "value": {"type": "reasoning", "text": "I think the answer is 42."},
    }
    events = list(parse_event(raw))
    assert events == [AgentEvent(kind="reasoning", data={"text": "I think the answer is 42."})]


def test_parses_reasoning_with_empty_text():
    raw = {
        "type": "TASK_PROGRESS",
        "value": {"type": "reasoning", "text": ""},
    }
    events = list(parse_event(raw))
    assert events == [AgentEvent(kind="reasoning", data={"text": ""})]
