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
from flux.agents.console.service import SessionPage, ApprovalRow, ConsoleService, SessionRow
from flux.agents.events import AgentEvent
from flux.agents.ui.console_screens import (
    ApprovalsScreen,
    ElicitationScreen,
    NewSessionScreen,
    RenameScreen,
)
from flux.agents.ui.textual_app import AMBER, ConsoleApp, derive_blocks, unwrap_output

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
        self.session_total = 0
        self.session_filters: list[str | None] = []
        self.elicitations: list[tuple] = []
        self.stops: list[tuple] = []
        self.sends: list[tuple[str, str]] = []

    async def list_agents(self):
        return list(self._agents)

    async def list_sessions(self, agent=None, limit=None, offset=0):
        # Filters for real, like GET /agents/sessions?agent=: a rail query
        # that quietly returned everything would hide exactly the bug where
        # a session falls outside the filter the console is asking with.
        self.session_filters.append(agent)
        rows = [row for row in self._sessions if agent is None or row.agent_name == agent]
        return SessionPage(rows=rows, total=self.session_total or len(rows))

    async def list_approvals(self):
        return list(self._approvals)

    async def get_detail(self, execution_id):
        return self._detail

    async def send(self, execution_id, agent_name, workflow_name, text):
        self.sends.append((execution_id, workflow_name))
        for frame in self._frames:
            yield frame

    async def spawn(self, agent_name, name):
        self.spawned.append((agent_name, name))
        # Mirrors ConsoleService.spawn: an agent with a workflow_file runs
        # under its own agent_custom_<name> workflow, and the new session
        # shows up in the next listing.
        self._sessions.append(
            SessionRow(
                execution_id="exec-new",
                agent_name=agent_name,
                state="RUNNING",
                name=name or None,
                started_at="2026-08-14T12:00:00",
                workflow_name=f"agent_custom_{agent_name}",
            ),
        )
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

    async def respond_to_elicitation(self, execution_id, workflow_name, payload):
        self.elicitations.append((execution_id, workflow_name, payload))

    async def stop(self, execution_id, namespace, workflow_name):
        self.stops.append((execution_id, namespace, workflow_name))

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


async def test_the_subset_row_widens_the_page_to_every_session():
    """The rail's "showing N of M" line used to document itself and stop --
    the sessions past the server page were unreachable from the console.
    Selecting it widens the page the refresh loop asks with (#245)."""
    service = _FakeService(sessions=[RUNNING_ROW])
    service.session_total = 100
    app = _console(service)
    async with app.run_test(size=(120, 32)) as pilot:
        await pilot.pause()
        assert app._session_limit is None
        assert "showing 1 of 100" in _rail_text(app)
        await pilot.press("1")
        # One row between the heading and the subset line, then select it.
        await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause()
        assert app._session_limit == 100
        assert app.query_one("#panel-sessions", OptionList).highlighted is not None


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


# ── `flux agent start NAME` opens a session ──────────────────────────


async def test_initial_agent_attaches_to_that_agent_most_recent_live_session():
    """Naming an agent is a request to talk to it, not merely to filter the
    rail by it: the console opens focused on a session of that agent."""
    older = SessionRow(
        execution_id="exec-old",
        agent_name="coder",
        state="PAUSED",
        name=None,
        started_at="2026-08-14T09:00:00",
        workflow_name="agent_chat",
    )
    service = _FakeService(sessions=[older, RUNNING_ROW])
    app = _console(service, agent="coder")
    async with app.run_test(size=(120, 32)) as pilot:
        await pilot.pause()
        assert app.active_session == "exec-1"  # the most recently started
        assert service.spawned == []


async def test_initial_agent_skips_finished_sessions_when_attaching():
    finished = SessionRow(
        execution_id="exec-done",
        agent_name="coder",
        state="COMPLETED",
        name=None,
        started_at="2026-08-14T23:00:00",  # newest, but nothing left to say
        workflow_name="agent_chat",
    )
    service = _FakeService(sessions=[finished, RUNNING_ROW])
    app = _console(service, agent="coder")
    async with app.run_test(size=(120, 32)) as pilot:
        await pilot.pause()
        assert app.active_session == "exec-1"


async def test_initial_agent_with_no_live_session_spawns_one():
    service = _FakeService(sessions=[])
    app = _console(service, agent="coder")
    async with app.run_test(size=(120, 32)) as pilot:
        await pilot.pause()
        assert service.spawned == [("coder", None)]
        assert app.active_session == "exec-new"


async def test_a_session_for_another_agent_joins_the_rail():
    """``initial_agent`` is an *initial* filter, not a permanent one.

    The new-session picker lists every agent, so the operator can start one
    outside it; if the rail keeps querying with the old filter that session
    is invisible to it, and every later action on it resolves against no
    row at all.
    """
    service = _FakeService(sessions=[RUNNING_ROW])
    app = _console(service, agent="coder")
    async with app.run_test(size=(120, 32)) as pilot:
        await pilot.pause()
        await app._create_session("writer", "")
        await pilot.pause()

        assert app.active_session == "exec-new"
        row = app._active_row()
        assert row is not None
        assert row.workflow_name == "agent_custom_writer"
        assert "exec-new" in {
            app.query_one("#panel-sessions", OptionList).get_option_at_index(index).id
            for index in range(app.query_one("#panel-sessions", OptionList).option_count)
        }


async def test_a_turn_on_a_session_outside_the_filter_resumes_its_own_workflow():
    """Resuming under ``agent_chat`` because the row was missing targets a
    workflow the execution does not belong to, and the turn fails."""
    service = _FakeService(sessions=[RUNNING_ROW])
    app = _console(service, agent="coder")
    async with app.run_test(size=(120, 32)) as pilot:
        await pilot.pause()
        await app._create_session("writer", "")
        await pilot.pause()
        await app.send_message_text("hello")
        await pilot.pause()

    assert service.sends == [("exec-new", "agent_custom_writer")]


async def test_a_turn_with_no_known_workflow_fails_loudly():
    """When the workflow genuinely cannot be resolved, say so -- silently
    falling back to ``agent_chat`` guesses at the one thing that decides
    where the turn lands."""
    service = _FakeService(sessions=[])
    app = _console(service)
    async with app.run_test(size=(120, 32)) as pilot:
        await pilot.pause()
        app.active_session = "exec-ghost"
        await app.send_message_text("hello")
        await pilot.pause()

        assert service.sends == []
        assert "unknown workflow" in _text(app.query_one("#status-line", Static))


# ── the rail highlight follows the session on screen ─────────────────


async def test_boot_on_a_resumed_session_highlights_it():
    """`r` and `x` act on the *highlighted* row: an open session the rail
    never highlighted means both act on a different session than the one the
    operator is looking at."""
    other = SessionRow(
        execution_id="exec-2",
        agent_name="coder",
        state="RUNNING",
        name="other",
        started_at="2026-08-14T11:00:00",
        workflow_name="agent_chat",
    )
    app = _console(_FakeService(sessions=[RUNNING_ROW, other]), session="exec-2")
    async with app.run_test(size=(120, 32)) as pilot:
        await pilot.pause()
        assert app.active_session == "exec-2"
        assert app._highlighted_session() == "exec-2"


async def test_attaching_to_an_agent_highlights_the_session_it_opened():
    older = SessionRow(
        execution_id="exec-old",
        agent_name="coder",
        state="RUNNING",
        name="older",
        started_at="2026-08-14T09:00:00",
        workflow_name="agent_chat",
    )
    app = _console(_FakeService(sessions=[older, RUNNING_ROW]), agent="coder")
    async with app.run_test(size=(120, 32)) as pilot:
        await pilot.pause()
        assert app.active_session == "exec-1"
        assert app._highlighted_session() == "exec-1"


async def test_creating_a_session_highlights_the_new_row():
    service = _FakeService(sessions=[RUNNING_ROW])
    app = _console(service, agent="coder")
    async with app.run_test(size=(120, 32)) as pilot:
        await pilot.pause()
        await app._create_session("coder", "")
        await pilot.pause()
        assert app.active_session == "exec-new"
        assert app._highlighted_session() == "exec-new"


async def test_stop_targets_the_session_on_screen_after_opening_it():
    """The end of the same bug: `x` armed and fired against the row the rail
    happened to highlight, not the session the operator opened."""
    other = SessionRow(
        execution_id="exec-2",
        agent_name="coder",
        state="RUNNING",
        name="other",
        started_at="2026-08-14T11:00:00",
        workflow_name="agent_custom_coder",
    )
    service = _FakeService(sessions=[RUNNING_ROW, other])
    app = _console(service, session="exec-2")
    async with app.run_test(size=(120, 32)) as pilot:
        await pilot.pause()
        await pilot.press("x")
        await pilot.press("x")
        await pilot.pause()

    assert service.stops == [("exec-2", "agents", "agent_custom_coder")]


async def test_initial_session_wins_over_initial_agent():
    service = _FakeService(sessions=[RUNNING_ROW])
    app = _console(service, agent="coder", session="exec-1")
    async with app.run_test(size=(120, 32)) as pilot:
        await pilot.pause()
        assert app.active_session == "exec-1"
        assert service.spawned == []


# ── elicitation: the terminal console can answer one ─────────────────


ELICITATION = {
    "type": "elicitation",
    "elicitation_id": "elicit-7",
    "server_name": "github",
    "message": "Authorize Flux to read your repos?",
    "url": "https://github.test/authorize",
}


async def test_elicitation_block_advertises_its_key():
    app = _console(_FakeService(sessions=[RUNNING_ROW]), session="exec-1")
    async with app.run_test(size=(120, 32)) as pilot:
        await pilot.pause()
        await app.handle_event(
            ConsoleEvent("exec-1", AgentEvent(kind="elicitation", data=ELICITATION)),
        )
        await pilot.pause()
        rendered = _panel_text(app, "#panel-chat")
        assert "Authorize Flux to read your repos?" in rendered
        assert "press e to answer" in rendered
        assert "e answer" in _text(app.query_one("#status-line", Static))


async def test_e_opens_the_elicitation_overlay_and_accepting_answers_it():
    """Without this the terminal console can see an MCP authorization prompt
    but never answer it, so every MCP auth flow hangs."""
    service = _FakeService(sessions=[RUNNING_ROW])
    app = _console(service, session="exec-1")
    async with app.run_test(size=(120, 32)) as pilot:
        await pilot.pause()
        await app.handle_event(
            ConsoleEvent("exec-1", AgentEvent(kind="elicitation", data=ELICITATION)),
        )
        await pilot.pause()
        await pilot.press("e")
        await pilot.pause()
        assert isinstance(app.screen, ElicitationScreen)
        await pilot.click("#accept")
        await pilot.pause()

    assert service.elicitations == [
        (
            "exec-1",
            "agent_chat",
            {"elicitation_response": {"elicitation_id": "elicit-7", "action": "accept"}},
        ),
    ]


async def test_answered_elicitation_is_marked_and_not_offered_again():
    service = _FakeService(sessions=[RUNNING_ROW])
    app = _console(service, session="exec-1")
    async with app.run_test(size=(120, 32)) as pilot:
        await pilot.pause()
        await app.handle_event(
            ConsoleEvent("exec-1", AgentEvent(kind="elicitation", data=ELICITATION)),
        )
        await pilot.pause()
        await pilot.press("e")
        await pilot.pause()
        await pilot.click("#decline")
        await pilot.pause()
        assert "decline" in _panel_text(app, "#panel-chat")
        assert app._pending_elicitation() is None


async def test_elicitation_answers_the_session_that_raised_it():
    """The block's own execution, not whichever session is open by the time
    the operator gets to it."""
    other = SessionRow(
        execution_id="exec-2",
        agent_name="coder",
        state="RUNNING",
        name="other",
        started_at="2026-08-14T11:00:00",
        workflow_name="agent_custom_coder",
    )
    service = _FakeService(sessions=[RUNNING_ROW, other])
    app = _console(service, session="exec-2")
    async with app.run_test(size=(120, 32)) as pilot:
        await pilot.pause()
        await app.handle_event(
            ConsoleEvent("exec-2", AgentEvent(kind="elicitation", data=ELICITATION)),
        )
        await pilot.pause()
        await pilot.press("e")
        await pilot.pause()
        await pilot.click("#accept")
        await pilot.pause()

    assert service.elicitations[0][0] == "exec-2"
    assert service.elicitations[0][1] == "agent_custom_coder"


async def test_elicitation_overlay_refuses_a_non_browsable_url():
    """The url comes from a remote MCP server; only absolute http(s) is ever
    offered to a browser."""
    service = _FakeService(sessions=[RUNNING_ROW])
    app = _console(service, session="exec-1")
    hostile = {**ELICITATION, "url": "file:///etc/passwd"}
    async with app.run_test(size=(120, 32)) as pilot:
        await pilot.pause()
        await app.handle_event(ConsoleEvent("exec-1", AgentEvent(kind="elicitation", data=hostile)))
        await pilot.pause()
        await pilot.press("e")
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, ElicitationScreen)
        assert len(screen.query("#open-url")) == 0
        assert "refusing a non-browsable url" in "\n".join(
            _text(node) for node in screen.query(Static)
        )


async def test_read_only_cannot_answer_an_elicitation():
    service = _FakeService(sessions=[RUNNING_ROW])
    app = _console(service, session="exec-1")
    async with app.run_test(size=(120, 32)) as pilot:
        await pilot.pause()
        app.can_write = False
        await app.handle_event(
            ConsoleEvent("exec-1", AgentEvent(kind="elicitation", data=ELICITATION)),
        )
        await pilot.pause()
        await pilot.press("e")
        await pilot.pause()
        assert not isinstance(app.screen, ElicitationScreen)
        assert "read-only" in _text(app.query_one("#status-line", Static))
        assert service.elicitations == []


# ── stop ─────────────────────────────────────────────────────────────


async def test_x_twice_stops_the_open_session():
    service = _FakeService(sessions=[RUNNING_ROW])
    app = _console(service, session="exec-1")
    async with app.run_test(size=(120, 32)) as pilot:
        await pilot.pause()
        await pilot.press("x")
        await pilot.pause()
        # Armed, not fired: a cancel is not undoable.
        assert service.stops == []
        assert "press x again to stop" in _text(app.query_one("#status-line", Static))
        await pilot.press("x")
        await pilot.pause()

    assert service.stops == [("exec-1", "agents", "agent_chat")]


async def test_stop_refuses_a_finished_session():
    finished = SessionRow(
        execution_id="exec-done",
        agent_name="coder",
        state="COMPLETED",
        name="done",
        started_at="2026-08-14T10:00:00",
        workflow_name="agent_chat",
    )
    service = _FakeService(sessions=[finished])
    app = _console(service, session="exec-done")
    async with app.run_test(size=(120, 32)) as pilot:
        await pilot.pause()
        await pilot.press("x")
        await pilot.press("x")
        await pilot.pause()
        assert service.stops == []
        assert "already finished" in _text(app.query_one("#status-line", Static))


async def test_read_only_cannot_stop_a_session():
    service = _FakeService(sessions=[RUNNING_ROW])
    app = _console(service, session="exec-1")
    async with app.run_test(size=(120, 32)) as pilot:
        await pilot.pause()
        app.can_write = False
        await pilot.press("x")
        await pilot.press("x")
        await pilot.pause()
        assert service.stops == []
        assert "read-only" in _text(app.query_one("#status-line", Static))


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


def test_derive_blocks_ignores_a_terminal_event_with_no_start():
    """Since #244 the engine records a start for every call, including calls
    that begin in a resumed run, so a terminal event with no start is an
    engine bug rather than a shape to reconstruct a tool from."""
    detail = {
        "execution_id": "exec-1",
        "events": [
            {
                "type": "WORKFLOW_RESUMED",
                "value": {"message": "search the docs"},
                "time": "2026-08-14T10:00:00",
                "name": "w",
            },
            {
                "type": "TASK_COMPLETED",
                "value": {
                    "storage_type": "inline",
                    "reference_id": "search_docs_1",
                    "metadata": {"value": "3 hits"},
                },
                "time": "2026-08-14T10:00:04",
                "name": "search_docs",
                "source_id": "call-9",
            },
        ],
    }

    _blocks, tools, _title = derive_blocks(detail)

    assert tools == {}


def test_derive_blocks_pairs_a_start_with_its_completion_after_a_resume():
    """The shape the engine actually produces now: both halves, joinable by
    source_id, so the panel has a name, args, output and a duration."""
    detail = {
        "execution_id": "exec-1",
        "events": [
            {
                "type": "WORKFLOW_RESUMED",
                "value": {"message": "search the docs"},
                "time": "2026-08-14T10:00:00",
                "name": "w",
            },
            {
                "type": "TASK_STARTED",
                "value": {"query": "schedules"},
                "time": "2026-08-14T10:00:02",
                "name": "search_docs",
                "source_id": "call-9",
            },
            {
                "type": "TASK_COMPLETED",
                "value": {
                    "storage_type": "inline",
                    "reference_id": "search_docs_1",
                    "metadata": {"value": "3 hits"},
                },
                "time": "2026-08-14T10:00:04",
                "name": "search_docs",
                "source_id": "call-9",
            },
        ],
    }

    _blocks, tools, _title = derive_blocks(detail)

    assert list(tools) == ["call-9"]
    tool = tools["call-9"]
    assert tool.name == "search_docs"
    assert tool.args == {"query": "schedules"}
    assert tool.output == "3 hits"
    assert tool.status == "success"
    assert tool.started is not None and tool.ended is not None


def test_unwrap_output_names_an_externally_stored_value():
    """A non-inline reference does not carry the value in the log at all, so
    the panel says where it went instead of dumping the envelope."""
    reference = {
        "storage_type": "local_file",
        "reference_id": "big_task_1",
        "metadata": {"serializer": "pkl"},
    }
    assert unwrap_output(reference) == "[stored in local_file: big_task_1]"
    # Anything that is not an envelope passes through untouched.
    assert unwrap_output({"rows": 3}) == {"rows": 3}
    assert unwrap_output("plain") == "plain"


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


async def test_a_tool_done_without_a_status_lands_on_the_web_default():
    """Parity with console.js's `data.status || "done"`: the two surfaces
    share one status vocabulary, so a frame that names no status must not
    resolve differently here."""
    app = _console(_FakeService(sessions=[RUNNING_ROW]), session="exec-1")
    async with app.run_test(size=(120, 32)) as pilot:
        await pilot.pause()
        await app.handle_event(
            ConsoleEvent(
                "exec-1",
                AgentEvent(kind="tool_start", data={"id": "t1", "name": "grep"}),
            ),
        )
        await app.handle_event(
            ConsoleEvent("exec-1", AgentEvent(kind="tool_done", data={"id": "t1"})),
        )
        await pilot.pause()
        assert app.tools["t1"].status == "done"


def test_the_tui_truncates_titles_the_way_the_server_does():
    """The rail, the status line and the server's derived_title all name the
    same session; three truncation rules meant three different names for it
    depending on which surface the operator was looking at."""
    from flux.agents.console.titles import derived_title
    from flux.agents.ui.textual_app import _truncate

    message = "reproduce the flaky scheduler test and explain what actually races"
    server_title = derived_title(
        {"events": [{"type": "WORKFLOW_RESUMED", "value": {"message": message}}]},
    )

    assert _truncate(message) == server_title


# ---------------------------------------------------------------------------
# Rail row geometry (#245)
# ---------------------------------------------------------------------------


def test_a_rail_row_never_exceeds_the_rail_width():
    """One row, one line: a row a column too wide either wraps -- reading as
    two sessions -- or ellipsizes away the state/age tail it was padded to
    keep visible."""
    from flux.agents.ui.textual_app import RAIL_TEXT_WIDTH, ConsoleApp

    app = ConsoleApp.__new__(ConsoleApp)
    # _bucket_of consults pending approvals; the rest of the app is not
    # needed to lay out one row.
    app.approvals = []

    long_titles = [
        "x" * 200,
        "Investigate the flaky integration suite before shipping the release",
        "word " * 40,
    ]
    for state in ("RUNNING", "PAUSED", "COMPLETED", "FAILED"):
        for title in long_titles:
            row = SessionRow(
                execution_id="exec-1",
                agent_name="coder",
                state=state,
                name=title,
                started_at="2026-08-14T10:00:00",
                workflow_name="agent_chat",
            )
            prompt = app._rail_prompt(row)

            assert len(prompt.plain) <= RAIL_TEXT_WIDTH, (state, prompt.plain)


def test_a_rail_row_keeps_its_state_tail_visible():
    """The tail is what the padding exists for -- a title allowed to eat it
    leaves the operator without the state or age of the session."""
    from flux.agents.ui.textual_app import ConsoleApp

    app = ConsoleApp.__new__(ConsoleApp)
    app.approvals = []
    row = SessionRow(
        execution_id="exec-1",
        agent_name="coder",
        state="RUNNING",
        name="x" * 200,
        started_at="2026-08-14T10:00:00",
        workflow_name="agent_chat",
    )

    prompt = app._rail_prompt(row)

    assert prompt.plain.rstrip().endswith("running") or "running" in prompt.plain


def test_the_elicitation_action_row_is_sized_like_the_approval_one():
    """The class was carried on the markup but had no rule, so the button
    row stayed a 1fr Horizontal and stretched to fill the overlay, pushing
    the key hint onto the bottom edge (#245)."""
    import re

    from flux.agents.ui.console_screens import ApprovalsScreen, ElicitationScreen

    rule = re.search(
        r"ElicitationScreen \.elicitation-actions \{(.*?)\}",
        ElicitationScreen.DEFAULT_CSS,
        re.DOTALL,
    )
    assert rule, "the action row must carry a rule, not just a class"
    assert "height: auto" in rule.group(1)
    # Same shape the sibling screen's action row already had.
    assert "height: auto" in re.search(
        r"ApprovalsScreen \.approval-actions \{(.*?)\}",
        ApprovalsScreen.DEFAULT_CSS,
        re.DOTALL,
    ).group(1)
