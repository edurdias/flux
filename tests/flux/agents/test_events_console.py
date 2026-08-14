"""Parse tests for the console-facing plan/subagent progress kinds.

Additive to flux/agents/events.py — see test_events.py for the base contract.
Regression cases below are copied from test_events.py to prove existing kinds
are untouched by the new branches.
"""

from __future__ import annotations

from flux.agents.events import (
    AgentEvent,
    KIND_PLAN,
    KIND_SUBAGENT,
    parse_event,
)


def test_parses_plan_progress():
    raw = {
        "type": "TASK_PROGRESS",
        "value": {"type": "plan", "plan": {"steps": []}},
    }
    events = list(parse_event(raw))
    assert events == [AgentEvent(kind=KIND_PLAN, data={"plan": {"steps": []}})]


def test_parses_plan_progress_with_steps():
    raw = {
        "type": "TASK_PROGRESS",
        "value": {
            "type": "plan",
            "plan": {
                "steps": [
                    {"name": "research", "description": "Research.", "status": "in_progress"},
                ],
            },
        },
    }
    events = list(parse_event(raw))
    assert len(events) == 1
    assert events[0].kind == KIND_PLAN
    assert events[0].data["plan"]["steps"][0]["name"] == "research"
    assert events[0].data["plan"]["steps"][0]["status"] == "in_progress"


def test_parses_subagent_started():
    raw = {
        "type": "TASK_PROGRESS",
        "value": {
            "type": "subagent",
            "call_id": "delegate_1",
            "agent": "researcher",
            "status": "started",
            "brief": "Find competitor pricing.",
        },
    }
    events = list(parse_event(raw))
    assert events == [
        AgentEvent(
            kind=KIND_SUBAGENT,
            data={
                "call_id": "delegate_1",
                "agent": "researcher",
                "status": "started",
                "brief": "Find competitor pricing.",
                "result_tail": "",
            },
        ),
    ]


def test_parses_subagent_done():
    raw = {
        "type": "TASK_PROGRESS",
        "value": {
            "type": "subagent",
            "call_id": "delegate_1",
            "status": "done",
            "result_tail": "Found 3 competitors.",
        },
    }
    events = list(parse_event(raw))
    assert events == [
        AgentEvent(
            kind=KIND_SUBAGENT,
            data={
                "call_id": "delegate_1",
                "agent": "",
                "status": "done",
                "brief": "",
                "result_tail": "Found 3 competitors.",
            },
        ),
    ]


def test_parses_subagent_failed():
    raw = {
        "type": "TASK_PROGRESS",
        "value": {
            "type": "subagent",
            "call_id": "delegate_2",
            "status": "failed",
            "result_tail": "boom",
        },
    }
    events = list(parse_event(raw))
    assert events == [
        AgentEvent(
            kind=KIND_SUBAGENT,
            data={
                "call_id": "delegate_2",
                "agent": "",
                "status": "failed",
                "brief": "",
                "result_tail": "boom",
            },
        ),
    ]


def test_parses_execution_id_alongside_plan_progress():
    """A state-carrying progress frame still yields the session_id handshake."""
    raw = {
        "execution_id": "exec-1",
        "type": "TASK_PROGRESS",
        "value": {"type": "plan", "plan": {"steps": []}},
    }
    events = list(parse_event(raw))
    assert AgentEvent(kind="session_id", data={"id": "exec-1"}) in events
    assert AgentEvent(kind=KIND_PLAN, data={"plan": {"steps": []}}) in events


# --- Regression: existing kinds must parse identically (copied from test_events.py) ---


def test_regression_parses_token_progress():
    raw = {"type": "TASK_PROGRESS", "value": {"token": "hello"}}
    events = list(parse_event(raw))
    assert events == [AgentEvent(kind="token", data={"text": "hello"})]


def test_regression_parses_tool_start():
    raw = {
        "type": "TASK_PROGRESS",
        "value": {"type": "tool_start", "id": "call_1", "name": "shell", "args": {"cmd": "ls"}},
    }
    events = list(parse_event(raw))
    assert events == [
        AgentEvent(
            kind="tool_start",
            data={"id": "call_1", "name": "shell", "args": {"cmd": "ls"}},
        ),
    ]


def test_regression_parses_reasoning_progress():
    raw = {
        "type": "TASK_PROGRESS",
        "value": {"type": "reasoning", "text": "I think the answer is 42."},
    }
    events = list(parse_event(raw))
    assert events == [AgentEvent(kind="reasoning", data={"text": "I think the answer is 42."})]


def test_regression_ignores_unknown_events():
    raw = {"type": "something.unrelated", "foo": "bar"}
    events = list(parse_event(raw))
    assert events == []
