"""Pilot tests for the Textual mission control (``ConsoleApp``).

Same house pattern as the other console tests: a ``ConsoleService``
subclass stands in for the HTTP-backed one, and the *real* ``EventHub``
runs on top of it, so fan-out and turn-boundary reconciliation are
exercised for real while nothing touches the network.
"""

from __future__ import annotations

import httpx
import pytest
from textual.color import Color
from textual.widgets import Collapsible, Input, OptionList, Static

from flux.agents.console.hub import ConsoleEvent, EventHub
from flux.agents.console.service import ApprovalRow, ConsoleService, SessionRow
from flux.agents.events import AgentEvent
from flux.agents.ui.console_screens import (
    ApprovalsScreen,
    NewSessionScreen,
    RenameScreen,
)
from flux.agents.ui.textual_app import AMBER, ConsoleApp, derive_blocks

DETAIL = {
    "execution_id": "exec-1",
    "events": [
        {"type": "WORKFLOW_STARTED", "value": None, "time": "2026-08-14T10:00:00", "name": "w"},
        {
            "type": "WORKFLOW_RESUMED",
            "value": {"message": "fix the CI"},
            "time": "2026-08-14T10:00:01",
            "name": "w",
        },
        {
            "type": "TASK_STARTED",
            "value": {"cmd": "pytest"},
            "time": "2026-08-14T10:00:02",
            "name": "shell",
            "source_id": "call-1",
        },
        {
            "type": "TASK_COMPLETED",
            "value": "ok",
            "time": "2026-08-14T10:00:03",
            "name": "shell",
            "source_id": "call-1",
        },
        {
            "type": "TASK_STARTED",
            "value": {},
            "time": "2026-08-14T10:00:04",
            "name": "llm_call",
            "source_id": "call-internal",
        },
        {
            "type": "WORKFLOW_PAUSED",
            "value": {"output": {"type": "chat_response", "content": "patched it", "turn": 1}},
            "time": "2026-08-14T10:00:05",
            "name": "w",
        },
    ],
}

RUNNING_ROW = SessionRow(
    execution_id="exec-1",
    agent_name="coder",
    state="RUNNING",
    name="fix CI",
    started_at="2026-08-14T10:00:00",
    workflow_name="agent_chat",
)


def _forbidden(permission: str) -> httpx.HTTPStatusError:
    request = httpx.Request("PUT", "http://test/x")
    response = httpx.Response(
        403,
        json={"detail": {"error": "forbidden", "missing_permission": permission}},
        request=request,
    )
    return httpx.HTTPStatusError("forbidden", request=request, response=response)


class _FakeService(ConsoleService):
    """Canned listings and streams in place of the HTTP client."""

    def __init__(
        self,
        sessions=None,
        approvals=None,
        agents=None,
        detail=None,
        frames=None,
        decide_result="decided",
        rename_error=None,
    ):
        super().__init__(server_url="http://test", token=None)
        self._sessions = sessions if sessions is not None else []
        self._approvals = approvals if approvals is not None else []
        self._agents = agents if agents is not None else [{"name": "coder"}, {"name": "writer"}]
        self._detail = detail if detail is not None else DETAIL
        self._frames = frames if frames is not None else []
        self._decide_result = decide_result
        self._rename_error = rename_error
        self.decisions: list[tuple] = []
        self.renames: list[tuple[str, str]] = []
        self.spawned: list[tuple[str, str | None]] = []
        self.session_filters: list[str | None] = []

    async def list_agents(self):
        return list(self._agents)

    async def list_sessions(self, agent=None):
        self.session_filters.append(agent)
        return list(self._sessions)

    async def list_approvals(self):
        return list(self._approvals)

    async def get_detail(self, execution_id):
        return self._detail

    async def send(self, execution_id, agent_name, workflow_name, text):
        for frame in self._frames:
            yield frame

    async def spawn(self, agent_name, name):
        self.spawned.append((agent_name, name))
        return "exec-new"

    async def decide(
        self,
        execution_id,
        task_call_id,
        approve,
        always=False,
        always_for_target=False,
    ):
        self.decisions.append((execution_id, task_call_id, approve, always, always_for_target))
        return self._decide_result

    async def rename(self, execution_id, name):
        if self._rename_error is not None:
            raise self._rename_error
        self.renames.append((execution_id, name))

    async def aclose(self):
        return None


def _console(service: _FakeService, agent=None, session=None) -> ConsoleApp:
    return ConsoleApp(service, EventHub(service), agent, session)


def _text(widget) -> str:
    return str(widget.render())


def _panel_text(app: ConsoleApp, selector: str) -> str:
    return "\n".join(_text(node) for node in app.query_one(selector).query(Static))


def _rail_text(app: ConsoleApp) -> str:
    rail = app.query_one("#panel-sessions", OptionList)
    return "\n".join(
        str(rail.get_option_at_index(index).prompt) for index in range(rail.option_count)
    )


async def test_console_composes_three_named_panels():
    app = _console(_FakeService(sessions=[RUNNING_ROW]))
    async with app.run_test(size=(120, 32)):
        assert app.query_one("#panel-sessions").border_title == "1sessions"
        assert app.query_one("#panel-chat").border_title == "2chat"
        assert app.query_one("#panel-context").border_title == "3context"
        assert app.query_one("#status-line") is not None


async def test_key_two_focuses_the_chat_panel():
    app = _console(_FakeService(sessions=[RUNNING_ROW]))
    async with app.run_test(size=(120, 32)) as pilot:
        await pilot.press("2")
        await pilot.pause()
        chat = app.query_one("#panel-chat")
        assert chat.has_class("-focused")
        assert chat.styles.border_top[1] == Color.parse(AMBER)
        assert not app.query_one("#panel-sessions").has_class("-focused")
        assert not app.query_one("#panel-context").has_class("-focused")


async def test_key_one_focuses_the_session_rail():
    app = _console(_FakeService(sessions=[RUNNING_ROW]))
    async with app.run_test(size=(120, 32)) as pilot:
        await pilot.press("2")
        await pilot.press("1")
        await pilot.pause()
        assert app.query_one("#panel-sessions").has_class("-focused")
        assert not app.query_one("#panel-chat").has_class("-focused")


async def test_key_n_pushes_the_new_session_overlay():
    app = _console(_FakeService())
    async with app.run_test(size=(120, 32)) as pilot:
        await pilot.press("n")
        await pilot.pause()
        assert isinstance(app.screen, NewSessionScreen)


async def test_new_session_overlay_filters_agents():
    app = _console(_FakeService(agents=[{"name": "coder"}, {"name": "writer"}]))
    async with app.run_test(size=(120, 32)) as pilot:
        await pilot.press("n")
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, NewSessionScreen)
        screen.query_one("#agent-filter", Input).value = "wri"
        await pilot.pause()
        options = screen.query_one("#agent-list", OptionList)
        prompts = [str(options.get_option_at_index(i).prompt) for i in range(options.option_count)]
        assert prompts == ["writer"]


async def test_narrow_width_hides_the_rail_and_context():
    app = _console(_FakeService(sessions=[RUNNING_ROW]))
    async with app.run_test(size=(120, 32)) as pilot:
        assert app.query_one("#panel-sessions").display is True
        await pilot.resize_terminal(90, 30)
        await pilot.pause()
        assert app.query_one("#panel-sessions").display is False
        assert app.query_one("#panel-context").display is False
        assert app.query_one("#panel-chat").display is True


async def test_hidden_rail_comes_back_with_its_hotkey():
    app = _console(_FakeService(sessions=[RUNNING_ROW]))
    async with app.run_test(size=(120, 32)) as pilot:
        await pilot.resize_terminal(90, 30)
        await pilot.pause()
        await pilot.press("1")
        await pilot.pause()
        assert app.query_one("#panel-sessions").display is True


async def test_very_narrow_is_chat_only():
    approval = ApprovalRow(
        execution_id="exec-1",
        task_call_id="call-1",
        task_name="shell",
        target_value=None,
        requested_at="2026-08-14T10:00:00",
    )
    app = _console(_FakeService(sessions=[RUNNING_ROW], approvals=[approval]))
    async with app.run_test(size=(120, 32)) as pilot:
        await pilot.resize_terminal(70, 30)
        await pilot.pause()
        assert app.query_one("#panel-sessions").display is False
        assert app.query_one("#panel-context").display is False
        assert app.query_one("#panel-chat").display is True
        assert "⚠1" in _text(app.query_one("#status-line", Static))


async def test_compact_status_carries_plan_progress_and_approvals():
    plan_frame = {
        "type": "TASK_PROGRESS",
        "value": {
            "type": "plan",
            "plan": {
                "steps": [
                    {"name": "reproduce", "status": "completed"},
                    {"name": "patch", "status": "in_progress"},
                ],
            },
        },
    }
    approval = ApprovalRow(
        execution_id="exec-1",
        task_call_id="call-1",
        task_name="shell",
        target_value=None,
        requested_at=None,
    )
    service = _FakeService(sessions=[RUNNING_ROW], approvals=[approval], frames=[plan_frame])
    app = _console(service, session="exec-1")
    async with app.run_test(size=(120, 32)) as pilot:
        await pilot.pause()
        await app.send_message_text("go")
        await pilot.pause()
        await pilot.resize_terminal(70, 30)
        await pilot.pause()
        status = _text(app.query_one("#status-line", Static))
        assert "step 1/2" in status
        assert "⚠1" in status


async def test_approvals_overlay_lists_rows_and_approves():
    approval = ApprovalRow(
        execution_id="exec-1",
        task_call_id="call-1",
        task_name="shell",
        target_value=None,
        requested_at="2026-08-14T10:00:00",
    )
    service = _FakeService(sessions=[RUNNING_ROW], approvals=[approval])
    app = _console(service)
    async with app.run_test(size=(120, 32)) as pilot:
        await pilot.pause()
        await pilot.press("a")
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, ApprovalsScreen)
        assert "shell" in "\n".join(_text(node) for node in screen.query(Static))
        await pilot.click("#approve-0")
        await pilot.pause()
        assert service.decisions == [("exec-1", "call-1", True, False, False)]


async def test_approvals_overlay_offers_always_for_target_only_with_a_target():
    with_target = ApprovalRow(
        execution_id="exec-1",
        task_call_id="call-1",
        task_name="deploy",
        target_value="prod",
        requested_at=None,
    )
    without_target = ApprovalRow(
        execution_id="exec-2",
        task_call_id="call-2",
        task_name="shell",
        target_value=None,
        requested_at=None,
    )
    service = _FakeService(approvals=[with_target, without_target])
    app = _console(service)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.press("a")
        await pilot.pause()
        screen = app.screen
        assert len(screen.query("#always-target-0")) == 1
        assert len(screen.query("#always-target-1")) == 0


async def test_already_decided_elsewhere_is_reported_on_the_row():
    approval = ApprovalRow(
        execution_id="exec-1",
        task_call_id="call-1",
        task_name="shell",
        target_value=None,
        requested_at=None,
    )
    service = _FakeService(approvals=[approval], decide_result="already_decided")
    app = _console(service)
    async with app.run_test(size=(120, 32)) as pilot:
        await pilot.pause()
        await pilot.press("a")
        await pilot.pause()
        await pilot.click("#approve-0")
        await pilot.pause()
        rendered = "\n".join(_text(node) for node in app.screen.query(Static))
        assert "already decided elsewhere" in rendered


async def test_rail_groups_failed_apart_from_done():
    rows = [
        RUNNING_ROW,
        SessionRow("exec-2", "coder", "PAUSED", None, None, "agent_chat"),
        SessionRow("exec-3", "coder", "COMPLETED", "triage", None, "agent_chat"),
        SessionRow("exec-4", "coder", "FAILED", "broken", None, "agent_chat"),
    ]
    app = _console(_FakeService(sessions=rows))
    async with app.run_test(size=(120, 32)) as pilot:
        await pilot.pause()
        rail = _rail_text(app)
        assert "ACTIVE" in rail
        assert "IDLE" in rail
        assert "DONE" in rail
        assert "FAILED" in rail
        assert rail.index("DONE") < rail.index("FAILED")
        assert "✗ broken" in rail


async def test_rail_shows_the_empty_state():
    app = _console(_FakeService(sessions=[]))
    async with app.run_test(size=(120, 32)) as pilot:
        await pilot.pause()
        assert "No sessions yet — press n for new session" in _rail_text(app)


async def test_context_panel_shows_empty_states():
    app = _console(_FakeService(sessions=[RUNNING_ROW]))
    async with app.run_test(size=(120, 32)) as pilot:
        await pilot.pause()
        rendered = _panel_text(app, "#panel-context")
        assert "No plan yet" in rendered
        assert "No tool calls yet" in rendered
        assert "No sub-agents" in rendered


async def test_chat_placeholder_until_a_session_is_open():
    app = _console(_FakeService(sessions=[RUNNING_ROW]))
    async with app.run_test(size=(120, 32)) as pilot:
        await pilot.pause()
        assert "No session open" in _panel_text(app, "#panel-chat")


async def test_opening_a_session_renders_its_log():
    app = _console(_FakeService(sessions=[RUNNING_ROW]), session="exec-1")
    async with app.run_test(size=(120, 32)) as pilot:
        await pilot.pause()
        rendered = _panel_text(app, "#panel-chat")
        assert "fix the CI" in rendered
        assert "patched it" in rendered


async def test_enter_on_a_rail_row_opens_that_session():
    app = _console(_FakeService(sessions=[RUNNING_ROW]))
    async with app.run_test(size=(120, 32)) as pilot:
        await pilot.pause()
        await pilot.press("1")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert app.active_session == "exec-1"


async def test_live_tokens_stream_into_the_chat():
    app = _console(_FakeService(sessions=[RUNNING_ROW]), session="exec-1")
    async with app.run_test(size=(120, 32)) as pilot:
        await pilot.pause()
        for chunk in ("hel", "lo"):
            await app.handle_event(
                ConsoleEvent("exec-1", AgentEvent(kind="token", data={"text": chunk})),
            )
        await pilot.pause()
        assert "hello" in _panel_text(app, "#panel-chat")


async def test_streaming_updates_one_widget_instead_of_rebuilding_the_log():
    """Tokens arrive dozens per second; tearing the transcript down and
    remounting it for each one is what makes a TUI feel broken."""
    app = _console(_FakeService(sessions=[RUNNING_ROW]), session="exec-1")
    async with app.run_test(size=(120, 32)) as pilot:
        await pilot.pause()
        await app.handle_event(
            ConsoleEvent("exec-1", AgentEvent(kind="token", data={"text": "one "})),
        )
        await pilot.pause()
        widget = app._stream_widget
        assert widget is not None
        await app.handle_event(
            ConsoleEvent("exec-1", AgentEvent(kind="token", data={"text": "two"})),
        )
        await pilot.pause()
        assert app._stream_widget is widget
        assert "one two" in _text(widget)


async def test_a_turn_reconciles_the_chat_against_the_log():
    """The stream is an overlay; the persisted log is what the chat shows."""
    frames = [
        {"type": "TASK_PROGRESS", "value": {"token": "hel"}},
        {
            "execution_id": "exec-1",
            "state": "PAUSED",
            "output": {"type": "chat_response", "content": "hello there", "turn": 1},
        },
    ]
    reconciled = {
        "execution_id": "exec-1",
        "events": [
            {"type": "WORKFLOW_RESUMED", "value": {"message": "hi"}, "time": None, "name": "w"},
            {
                "type": "WORKFLOW_PAUSED",
                "value": {"output": {"type": "chat_response", "content": "hello there"}},
                "time": None,
                "name": "w",
            },
        ],
    }
    service = _FakeService(sessions=[RUNNING_ROW], frames=frames, detail=reconciled)
    app = _console(service, session="exec-1")
    async with app.run_test(size=(120, 32)) as pilot:
        await pilot.pause()
        await app.send_message_text("hi")
        await pilot.pause()
        rendered = _panel_text(app, "#panel-chat")
        assert "hi" in rendered
        assert "hello there" in rendered


async def test_rename_overlay_renames_the_highlighted_row():
    service = _FakeService(sessions=[RUNNING_ROW])
    app = _console(service)
    async with app.run_test(size=(120, 32)) as pilot:
        await pilot.pause()
        await pilot.press("1")
        await pilot.pause()
        await pilot.press("r")
        await pilot.pause()
        assert isinstance(app.screen, RenameScreen)
        app.screen.query_one("#rename-input", Input).value = "ci triage"
        await pilot.press("enter")
        await pilot.pause()
        assert service.renames == [("exec-1", "ci triage")]


async def test_a_denied_write_degrades_to_read_only_and_names_the_permission():
    service = _FakeService(
        sessions=[RUNNING_ROW],
        rename_error=_forbidden("execution:name:write"),
    )
    app = _console(service)
    async with app.run_test(size=(120, 32)) as pilot:
        await pilot.pause()
        await pilot.press("1")
        await pilot.pause()
        await pilot.press("r")
        await pilot.pause()
        app.screen.query_one("#rename-input", Input).value = "ci triage"
        await pilot.press("enter")
        await pilot.pause()
        assert app.can_write is False
        status = _text(app.query_one("#status-line", Static))
        assert "execution:name:write" in status
        assert "read-only" in status


async def test_enter_expands_the_focused_tool_and_plan_rows():
    plan = {"steps": [{"name": "reproduce", "status": "completed", "description": "run it"}]}
    app = _console(_FakeService(sessions=[RUNNING_ROW]), session="exec-1")
    async with app.run_test(size=(120, 32)) as pilot:
        await pilot.pause()
        await app.handle_event(
            ConsoleEvent("exec-1", AgentEvent(kind="plan", data={"plan": plan})),
        )
        await pilot.pause()
        for panel in ("3", "2"):
            await pilot.press(panel)
            await pilot.press("tab")
            await pilot.press("enter")
            await pilot.pause()
        expanded = [node for node in app.query(Collapsible) if not node.collapsed]
        titles = [str(node.title) for node in expanded]
        assert any("reproduce" in title for title in titles), titles
        assert any("shell" in title for title in titles), titles


async def test_enter_steps_into_the_composer_and_escape_steps_back():
    """The composer must not hold focus by default: a focused text field
    swallows every printable key, and the hotkeys are the whole grammar."""
    app = _console(_FakeService(sessions=[RUNNING_ROW]), session="exec-1")
    async with app.run_test(size=(120, 32)) as pilot:
        await pilot.pause()
        await pilot.press("2")
        await pilot.pause()
        assert app.query_one("#chat-input", Input).has_focus is False
        await pilot.press("enter")
        await pilot.pause()
        assert app.query_one("#chat-input", Input).has_focus is True
        await pilot.press("escape")
        await pilot.pause()
        assert app.query_one("#chat-input", Input).has_focus is False
        assert app.query_one("#panel-chat").has_class("-focused")


async def test_read_only_dims_the_write_hints_in_the_status_line():
    service = _FakeService(
        sessions=[RUNNING_ROW],
        rename_error=_forbidden("execution:name:write"),
    )
    app = _console(service)
    async with app.run_test(size=(120, 32)) as pilot:
        await pilot.pause()
        status = app.query_one("#status-line", Static)
        assert "dim" not in str(status.render().markup)
        await pilot.press("r")
        await pilot.pause()
        app.screen.query_one("#rename-input", Input).value = "x"
        await pilot.press("enter")
        await pilot.pause()
        assert "dim" in str(app.query_one("#status-line", Static).render().markup)


async def test_ctrl_d_quits():
    app = _console(_FakeService(sessions=[RUNNING_ROW]))
    async with app.run_test(size=(120, 32)) as pilot:
        await pilot.pause()
        await pilot.press("ctrl+d")
        await pilot.pause()
        assert app.is_running is False


async def test_initial_agent_filters_the_rail_query():
    service = _FakeService(sessions=[RUNNING_ROW])
    app = _console(service, agent="coder")
    async with app.run_test(size=(120, 32)) as pilot:
        await pilot.pause()
        assert service.session_filters and service.session_filters[0] == "coder"


def test_derive_blocks_mirrors_the_web_log_semantics():
    blocks, tools, title = derive_blocks(DETAIL)
    kinds = [block.kind for block in blocks]
    assert kinds == ["user", "tool", "agent"]
    assert blocks[0].text == "fix the CI"
    assert blocks[2].text == "patched it"
    assert title == "fix the CI"
    # Internal machinery tasks are log events but not tool calls.
    assert list(tools) == ["call-1"]
    assert tools["call-1"].status == "success"


@pytest.mark.parametrize(
    ("state", "glyph"),
    [("RUNNING", "●"), ("PAUSED", "◐"), ("COMPLETED", "○"), ("FAILED", "✗")],
)
async def test_rail_glyphs_follow_the_design_system(state: str, glyph: str):
    row = SessionRow("exec-9", "coder", state, "row", None, "agent_chat")
    app = _console(_FakeService(sessions=[row]))
    async with app.run_test(size=(120, 32)) as pilot:
        await pilot.pause()
        assert f"{glyph} row" in _rail_text(app)


async def test_waiting_approval_glyph_wins_over_running():
    approval = ApprovalRow("exec-1", "call-1", "shell", None, None)
    app = _console(_FakeService(sessions=[RUNNING_ROW], approvals=[approval]))
    async with app.run_test(size=(120, 32)) as pilot:
        await pilot.pause()
        assert "◔ fix CI" in _rail_text(app)
