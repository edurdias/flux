"""``ConsoleApp`` -- the terminal mission control.

The btop-grammar counterpart of the web console: three numbered panels
whose names ride in the border, hotkeys to focus them, overlays for the
actions that need the keyboard, and a status line that carries the session
state. ``flux agent start`` opens this; ``--plain`` opens the line-based
REPL in ``terminal.py`` instead.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.widget import Widget
from textual.widgets import Collapsible, Input, OptionList, Static
from textual.widgets.option_list import Option

from flux.agents.console.errors import error_detail, missing_permission_of
from flux.agents.console.hub import KIND_LOG_DELTA, ConsoleEvent, EventHub
from flux.agents.console.service import ApprovalRow, ConsoleService, SessionRow
from flux.agents.console.titles import MAX_TITLE_LEN, truncate_title
from flux.agents.events import (
    KIND_APPROVAL_REQUIRED,
    KIND_CHAT_RESPONSE,
    KIND_ELICITATION,
    KIND_ERROR,
    KIND_PLAN,
    KIND_REASONING,
    KIND_SESSION_END,
    KIND_SUBAGENT,
    KIND_TOKEN,
    KIND_TOOL_DONE,
    KIND_TOOL_START,
)
from flux.agents.ui.console_screens import (
    ApprovalsScreen,
    ElicitationScreen,
    NewSessionScreen,
    RenameScreen,
)
from flux.agents.ui.textual_widgets import (
    ChatPanel,
    ContextPanel,
    SessionRail,
)

logger = logging.getLogger(__name__)

# ══ Console — terminal mission control ═══════════════════════════════

# The web console's dark tokens mapped to Textual CSS: one identity across
# both surfaces -- ink ground, hairline panel lines, amber for focus and
# live figures.
INK = "#0b111c"
PANEL = "#0e1524"
LINE = "#1c2536"
TEXT = "#c9d2e0"
MUTED = "#8b96a8"
AMBER = "#f0a828"
OK = "#4ade80"
DANGER = "#f87171"

# Status glyphs are part of the design system, not decoration: failed is
# never bucketed with done.
GLYPH = {"running": "●", "waiting": "◔", "idle": "◐", "done": "○", "failed": "✗"}
GLYPH_STYLE = {
    "running": OK,
    "waiting": AMBER,
    "idle": MUTED,
    "done": MUTED,
    "failed": DANGER,
}

TERMINAL_STATES = frozenset({"COMPLETED", "FAILED", "CANCELLED"})

RUNNING_STATES = frozenset(
    {
        "CREATED",
        "SCHEDULED",
        "CLAIMED",
        "RUNNING",
        "RESUMING",
        "RESUME_SCHEDULED",
        "RESUME_CLAIMED",
        "CANCELLING",
    },
)

# Tasks the runtime records for its own machinery -- the LLM call, the chat
# pause, config reads, working-memory bookkeeping. Real log events, but not
# tool calls; showing them would bury the ones that are. Same filter (and
# the same documented cost) as the web console.
INTERNAL_TASK = re.compile(r"^(pause$|get_config|get_secret|agent_|llm_|wm_)")

RAIL_REFRESH_SECONDS = 3.0
NARROW_WIDTH = 100  # below this the rail and context panels fold away
CHAT_ONLY_WIDTH = 80  # below this the status line goes compact
TITLE_LIMIT = MAX_TITLE_LEN  # one limit, shared with the server's derived_title
RAIL_WIDTH = 32
RAIL_TEXT_WIDTH = RAIL_WIDTH - 4  # minus the panel's border and padding

_PANELS = {
    "sessions": "#panel-sessions",
    "chat": "#panel-chat",
    "context": "#panel-context",
}


@dataclass
class ToolCall:
    """One tool call, shared by its chat block and its ACTIVITY row."""

    id: str
    name: str
    args: Any = None
    output: Any = None
    status: str = "running"
    started: float | None = None
    ended: float | None = None


@dataclass
class ChatBlock:
    """A rendered unit of transcript: a message, a tool call, a prompt."""

    kind: str
    text: str = ""
    time: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
    tool: ToolCall | None = None
    decided: str | None = None
    # The execution a prompt was raised in. Answers go back to it, never to
    # whichever session happens to be open when the operator gets to it.
    session_id: str | None = None


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _epoch(value: Any) -> float | None:
    parsed = _parse_time(value)
    return None if parsed is None else parsed.timestamp()


def _truncate(text: str, limit: int = TITLE_LIMIT) -> str:
    # Collapse first (a rail row is exactly one line), then cut on a word
    # boundary through the server's own helper, so a session is not called
    # one thing here and another in the derived_title the API serves.
    return truncate_title(" ".join(text.split()), limit)


def _fmt_elapsed(seconds: float | None) -> str:
    if seconds is None:
        return ""
    if seconds < 60:
        return f"{seconds:.1f}s"
    if seconds < 3600:
        return f"{seconds / 60:.1f}m"
    return f"{seconds / 3600:.1f}h"


def _fmt_age(value: Any) -> str:
    started = _parse_time(value)
    if started is None:
        return ""
    delta = (datetime.now(timezone.utc) - started).total_seconds()
    if delta < 60:
        return "now"
    if delta < 3600:
        return f"{int(delta // 60)}m"
    if delta < 86400:
        return f"{int(delta // 3600)}h"
    return f"{int(delta // 86400)}d"


def unwrap_output(value: Any) -> Any:
    """The value a task returned, out of its output-storage envelope.

    Every ``TASK_COMPLETED`` value in the log is an
    ``OutputStorageReference`` dict (flux/output_storage.py), not the return
    value itself; the default inline storage parks the real value under
    ``metadata.value``. Rendering the envelope verbatim would show operators
    storage bookkeeping where the tool's output belongs. A non-inline
    reference genuinely does not carry the value in the log, so name where
    it went instead of dumping the envelope. Same semantics as the web
    console's ``unwrapOutput``.
    """
    if not isinstance(value, dict) or "storage_type" not in value:
        return value
    metadata = value.get("metadata")
    if value.get("storage_type") == "inline" and isinstance(metadata, dict):
        return metadata.get("value")
    return f"[stored in {value.get('storage_type')}: {value.get('reference_id')}]"


def derive_blocks(
    detail: dict[str, Any],
) -> tuple[list[ChatBlock], dict[str, ToolCall], str | None]:
    """Rebuild a transcript from an execution detail dict.

    Only the log can express what actually happened, so this -- not the
    live stream -- is authoritative for chat blocks and tool calls. Plan
    and sub-agent state come from progress events, which are never
    persisted (see flux/tasks/progress.py), so they are deliberately left
    alone here rather than blanked on every turn boundary. The semantics
    mirror the web console's ``applyDetail``.

    Returns the blocks, the tool calls keyed by id, and a title derived
    from the session's first user message (the pre-poll display fallback).
    """
    blocks: list[ChatBlock] = []
    tools: dict[str, ToolCall] = {}
    approvals: dict[str, ChatBlock] = {}
    first_message: str | None = None

    for event in detail.get("events") or []:
        event_type = event.get("type")
        value = event.get("value")
        when = event.get("time")

        if event_type == "WORKFLOW_RESUMED":
            message = value.get("message") if isinstance(value, dict) else None
            if isinstance(message, str) and message.strip():
                if first_message is None:
                    first_message = message.strip()
                blocks.append(ChatBlock("user", text=message, time=when))

        elif event_type == "WORKFLOW_PAUSED":
            output = value.get("output") if isinstance(value, dict) else None
            if not isinstance(output, dict):
                continue
            output_type = output.get("type")
            if output_type == KIND_CHAT_RESPONSE and output.get("content"):
                blocks.append(ChatBlock("agent", text=str(output["content"]), time=when))
            elif output_type == KIND_ELICITATION:
                blocks.append(
                    ChatBlock(
                        "elicitation",
                        data=output,
                        time=when,
                        session_id=detail.get("execution_id"),
                    ),
                )
            elif output_type == KIND_SESSION_END:
                blocks.append(
                    ChatBlock(
                        "system",
                        text=(
                            f"Session ended: {output.get('reason', '')} "
                            f"({output.get('turns', 0)} turns)"
                        ),
                        time=when,
                    ),
                )

        elif event_type == "TASK_STARTED":
            name = event.get("name") or ""
            if INTERNAL_TASK.match(name):
                continue
            tool = ToolCall(
                id=str(event.get("source_id") or name),
                name=name,
                args=value,
                started=_epoch(when),
            )
            tools[tool.id] = tool
            blocks.append(ChatBlock("tool", tool=tool, time=when))

        elif event_type in ("TASK_COMPLETED", "TASK_FAILED", "TASK_CANCELLED"):
            started_tool = tools.get(str(event.get("source_id")))
            if started_tool is None:
                # A completion with no start: every task begun in a resumed
                # run looked like this until #244 (task.py suppressed
                # TASK_STARTED for the whole resumed run), which is every
                # tool call after a session's first turn. Executions logged
                # before that fix still read back this way, so the recovery
                # stays — dropping these would blank tool activity from an
                # existing transcript at every turn boundary.
                name = event.get("name") or ""
                if INTERNAL_TASK.match(name):
                    continue
                started_tool = ToolCall(
                    id=str(event.get("source_id") or name),
                    name=name,
                    # No start event means no start time; leave it unset so
                    # the duration is omitted rather than invented.
                    started=None,
                )
                tools[started_tool.id] = started_tool
                blocks.append(ChatBlock("tool", tool=started_tool, time=when))
            started_tool.status = {
                "TASK_COMPLETED": "success",
                "TASK_FAILED": "failed",
            }.get(event_type, "cancelled")
            started_tool.output = unwrap_output(value)
            started_tool.ended = _epoch(when)

        elif event_type == "TASK_AWAITING_APPROVAL":
            payload = value if isinstance(value, dict) else {}
            block = ChatBlock(
                "approval",
                time=when,
                data={
                    "execution_id": detail.get("execution_id"),
                    "task_call_id": payload.get("task_call_id") or event.get("source_id"),
                    "task_name": payload.get("task_name") or event.get("name"),
                    "target_value": payload.get("target_value"),
                },
            )
            approvals[str(event.get("source_id"))] = block
            blocks.append(block)

        elif event_type in ("TASK_APPROVED", "TASK_REJECTED"):
            gate = approvals.get(str(event.get("source_id")))
            if gate is not None:
                gate.decided = "approved" if event_type == "TASK_APPROVED" else "rejected"

        elif event_type == "WORKFLOW_FAILED":
            message = value.get("message") if isinstance(value, dict) else value
            blocks.append(ChatBlock("error", text=str(message or "Execution failed"), time=when))

        elif event_type == "WORKFLOW_CANCELLED":
            blocks.append(ChatBlock("system", text="Execution cancelled", time=when))

    return blocks, tools, (_truncate(first_message) if first_message else None)


CONSOLE_CSS = f"""
Screen {{
    background: {INK};
    color: {TEXT};
}}

#panels {{
    height: 1fr;
}}

/* Square corners, name in the top edge: btop's panel grammar. */
.panel {{
    background: {PANEL};
    border: solid {LINE};
    border-title-color: {MUTED};
    border-title-align: left;
    padding: 0 1;
    scrollbar-size: 1 1;
    scrollbar-background: {PANEL};
    scrollbar-color: {LINE};
}}

.panel.-focused {{
    border: solid {AMBER};
    border-title-color: {AMBER};
}}

#panel-sessions {{
    width: {RAIL_WIDTH};
    height: 1fr;
    background: {PANEL};
}}

#panel-sessions > .option-list--option-highlighted {{
    background: {LINE};
    color: {TEXT};
}}

#panel-chat {{
    width: 1fr;
    height: 1fr;
}}

#panel-context {{
    width: 30;
    height: 1fr;
}}

#chat-log {{
    height: 1fr;
    scrollbar-size: 1 1;
    scrollbar-background: {PANEL};
    scrollbar-color: {LINE};
}}

#chat-input {{
    dock: bottom;
    height: 3;
    background: {PANEL};
    border: hkey {LINE};
}}

#chat-input:focus {{
    border: hkey {AMBER};
}}

#status-line {{
    dock: bottom;
    height: 1;
    background: {INK};
    color: {MUTED};
    padding: 0 1;
}}

.chat-user, .chat-agent, .chat-system, .chat-error, .chat-empty, .chat-prompt {{
    height: auto;
    padding: 0 0 1 0;
    background: transparent;
}}

#chat-log > Collapsible {{
    padding: 0 0 1 0;
}}

.section-label {{
    color: {MUTED};
    text-style: bold;
    padding: 1 0 0 0;
}}

.empty {{
    color: {MUTED};
    padding: 0 0 1 0;
}}

.hero {{
    color: {AMBER};
    text-style: bold;
}}

Collapsible {{
    background: transparent;
    border: none;
    padding: 0;
}}

Collapsible > Contents {{
    background: transparent;
    padding: 0 0 0 2;
}}

CollapsibleTitle {{
    background: transparent;
    color: {MUTED};
    padding: 0;
}}

CollapsibleTitle:focus {{
    color: {AMBER};
}}
"""


class ConsoleApp(App):
    """Terminal mission control: three numbered panels, btop grammar.

    Constructed by the CLI with the same in-process ``ConsoleService`` and
    ``EventHub`` the web console uses, so both surfaces read one server
    client and one fan-out layer. The rail polls ``list_sessions`` on a
    timer (cheap, and the only way to see sessions this process did not
    start); an open session renders its persisted log first and applies
    live hub events on top -- the log is truth, the stream is an overlay.
    """

    CSS = CONSOLE_CSS

    BINDINGS = [
        Binding("1", "panel('sessions')", "sessions"),
        Binding("2", "panel('chat')", "chat"),
        Binding("3", "panel('context')", "context"),
        Binding("n", "new_session", "new"),
        Binding("a", "approvals", "approvals"),
        Binding("e", "elicitation", "answer"),
        Binding("r", "rename", "rename"),
        Binding("x", "stop_session", "stop"),
        Binding("enter", "compose", "write", show=False),
        Binding("escape", "leave", "back", show=False),
        Binding("ctrl+d", "quit_console", "quit", priority=True, show=False),
    ]

    def __init__(
        self,
        service: ConsoleService,
        hub: EventHub,
        initial_agent: str | None = None,
        initial_session: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.service = service
        self.hub = hub
        self.initial_agent = initial_agent
        self.initial_session = initial_session
        # The rail's *current* agent filter. It starts at initial_agent
        # (naming an agent asks for its sessions first) but is only an
        # initial view: the new-session picker lists every agent, and a
        # session the rail cannot see is one every later action resolves
        # against the wrong row -- or no row at all. Dropped for good as
        # soon as a session outside it is created or opened.
        self._agent_filter = initial_agent

        self.sessions: list[SessionRow] = []
        self.approvals: list[ApprovalRow] = []
        self.agents: list[str] = []
        self.active_session: str | None = None

        self.can_write: bool = True
        self.missing_permission: str | None = None
        self.notice: str | None = None

        self.blocks: list[ChatBlock] = []
        self.tools: dict[str, ToolCall] = {}
        self.plan: dict[str, Any] | None = None
        self.subagents: dict[str, dict[str, Any]] = {}

        self._queue: asyncio.Queue[ConsoleEvent] | None = None
        self._stream_text: str = ""
        self._stream_widget: Static | None = None
        self._reasoning: ChatBlock | None = None
        self._local_title: str | None = None
        # The open session's workflow, straight from its detail read -- the
        # authority whenever the rail has not caught up with it yet.
        self._active_workflow: str | None = None
        self._busy: bool = False
        self._compact: bool = False
        self._minimal: bool = False
        self._hidden: set[str] = set()
        self._focused_panel: str = "sessions"
        self._context_signature: tuple | None = None
        self._layout_ready: bool = False
        # Stop is armed by one `x` and fired by the next: cancelling a
        # session cannot be undone, and `x` sits under the same fingers as
        # the other single-key actions.
        self._stop_armed: str | None = None

    # ── composition ─────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        with Horizontal(id="panels"):
            yield SessionRail(id="panel-sessions", classes="panel")
            yield ChatPanel(id="panel-chat", classes="panel")
            yield ContextPanel(id="panel-context", classes="panel")
        yield Static("", id="status-line")

    async def on_mount(self) -> None:
        self._queue = self.hub.subscribe()
        self.run_worker(self._consume_events(), name="console-events", group="console")
        self._apply_layout(self.size.width)
        self._focus_panel("sessions")
        self._render_rail()
        await self._render_chat()
        await self._render_context()
        self._update_status()
        self._layout_ready = True
        self.run_worker(self._bootstrap(), name="console-bootstrap", group="console")
        self.set_interval(RAIL_REFRESH_SECONDS, self._schedule_refresh)

    def on_unmount(self) -> None:
        if self._queue is not None:
            self.hub.unsubscribe(self._queue)
            self._queue = None

    # ── data ────────────────────────────────────────────────────────

    async def _bootstrap(self) -> None:
        agents = await self._read(self.service.list_agents(), "agents")
        if agents is not None:
            self.agents = [str(agent.get("name")) for agent in agents if agent.get("name")]
        await self._refresh_lists()
        if self.initial_session:
            await self.open_session(self.initial_session)
            return
        if self.initial_agent:
            # `flux agent start NAME` opens focused on a session of that
            # agent (the binding spec's NAME semantics, and what the web
            # console does on boot) -- naming an agent is a request to talk
            # to it, not merely to filter the rail by it.
            await self._attach_to_agent(self.initial_agent)

    async def _attach_to_agent(self, agent_name: str) -> None:
        """Open that agent's most recent live session, or start one."""
        # self.sessions is already filtered to this agent (it is the rail
        # query's filter), so "most recent still-answerable session" is a
        # pick over what the rail is showing.
        candidates = [
            row
            for row in self.sessions
            if row.agent_name == agent_name and (row.state or "").upper() not in TERMINAL_STATES
        ]
        if candidates:
            newest = max(candidates, key=lambda row: row.started_at or "")
            await self.open_session(newest.execution_id)
            return
        await self._create_session(agent_name, "")

    def _schedule_refresh(self) -> None:
        self.run_worker(
            self._refresh_lists(),
            name="console-refresh",
            group="console-refresh",
            exclusive=True,
        )

    async def _refresh_lists(self) -> None:
        sessions = await self._read(self.service.list_sessions(self._agent_filter), "sessions")
        if sessions is not None:
            self.sessions = sessions
        approvals = await self._read(self.service.list_approvals(), "approvals")
        if approvals is not None:
            self.approvals = approvals
        self._render_rail()
        self._update_status()

    async def _read(self, coro: Any, label: str) -> Any:
        try:
            return await coro
        except Exception as exc:  # a listing that fails must not kill the app
            self._note_failure(exc, label, is_write=False)
            return None

    async def _write(self, coro: Any, label: str) -> Any:
        try:
            return await coro
        except Exception as exc:
            self._note_failure(exc, label, is_write=True)
            return None

    def _note_failure(self, exc: Exception, label: str, is_write: bool) -> None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status == 403:
            permission = missing_permission_of(error_detail(exc))
            if permission:
                self.missing_permission = permission
            # Only a denied *write* means read-only; a 403 on a listing is a
            # different permission gap entirely, and the console learns this
            # lazily -- in-process there is no /console/state to ask first.
            if is_write:
                self.can_write = False
                required = self.missing_permission or "a write permission"
                self.notice = f"read-only: requires {required}"
                self._update_status()
                return
        logger.warning("console: %s failed", label, exc_info=True)
        self.notice = f"{label}: {exc}"
        self._update_status()

    # ── live events ─────────────────────────────────────────────────

    async def _consume_events(self) -> None:
        queue = self._queue
        if queue is None:
            return
        while True:
            envelope = await queue.get()
            try:
                await self.handle_event(envelope)
            except Exception:  # one bad frame must not stop the feed
                logger.warning("console: failed to render event", exc_info=True)

    async def handle_event(self, envelope: ConsoleEvent) -> None:
        """Apply one hub event. The single entry point for live state."""
        kind = envelope.event.kind
        data = envelope.event.data or {}
        active = envelope.session_id == self.active_session

        if kind == KIND_LOG_DELTA:
            if active:
                detail = data.get("detail")
                if detail:
                    self.blocks, self.tools, self._local_title = derive_blocks(detail)
                else:
                    # Reconciliation failed: keep the last known state, say
                    # so, and let the next turn (or the poll) catch up.
                    reason = data.get("error") or "unknown error"
                    self.blocks.append(
                        ChatBlock("error", text=f"Could not reconcile the execution log: {reason}"),
                    )
                self._stream_text = ""
                self._reasoning = None
                await self._render_chat()
                await self._render_context()
            await self._refresh_lists()
            return

        if kind == KIND_APPROVAL_REQUIRED:
            if active:
                self.blocks.append(
                    ChatBlock(
                        "approval",
                        time=data.get("requested_at"),
                        data={
                            "execution_id": data.get("execution_id") or envelope.session_id,
                            "task_call_id": data.get("task_call_id"),
                            "task_name": data.get("task_name"),
                            "target_value": data.get("target_value"),
                        },
                    ),
                )
                await self._render_chat()
            # The status line's ⚠ count should light up without waiting for
            # the poll.
            await self._refresh_lists()
            return

        if not active:
            return

        if kind == KIND_TOKEN:
            self._stream_text += str(data.get("text") or "")
            await self._render_stream()
        elif kind == KIND_REASONING:
            if self._reasoning is None:
                self._reasoning = ChatBlock("reasoning")
                self.blocks.append(self._reasoning)
            self._reasoning.text += str(data.get("text") or "")
            await self._render_chat()
        elif kind == KIND_TOOL_START:
            tool = ToolCall(
                id=str(data.get("id") or f"tool-{len(self.tools)}"),
                name=str(data.get("name") or "tool"),
                args=data.get("args"),
                started=time.time(),
            )
            self.tools[tool.id] = tool
            self.blocks.append(ChatBlock("tool", tool=tool))
            await self._render_chat()
            await self._render_context()
        elif kind == KIND_TOOL_DONE:
            running_tool = self.tools.get(str(data.get("id")))
            if running_tool is not None:
                # Same default as console.js's `data.status || "done"`: the
                # two surfaces share one status vocabulary.
                running_tool.status = str(data.get("status") or "done")
                running_tool.ended = time.time()
                await self._render_chat()
                await self._render_context()
        elif kind == KIND_CHAT_RESPONSE:
            content = data.get("content") or self._stream_text
            self._stream_text = ""
            self._reasoning = None
            if content:
                self.blocks.append(ChatBlock("agent", text=str(content)))
            await self._render_chat()
        elif kind == KIND_ELICITATION:
            self.blocks.append(
                ChatBlock("elicitation", data=dict(data), session_id=envelope.session_id),
            )
            await self._render_chat()
            self._update_status()
        elif kind == KIND_PLAN:
            self.plan = data.get("plan") or None
            await self._render_context()
            self._update_status()
        elif kind == KIND_SUBAGENT:
            call_id = str(data.get("call_id") or "subagent")
            card = self.subagents.get(call_id) or {
                "id": call_id,
                "agent": "",
                "brief": "",
                "result_tail": "",
                "status": "started",
                "started": time.time(),
            }
            # The closing frame carries only call_id/status/result_tail, so
            # merge rather than replace or the agent name would disappear.
            for key in ("agent", "brief", "result_tail", "status"):
                if data.get(key):
                    card[key] = data[key]
            self.subagents[call_id] = card
            await self._render_context()
        elif kind == KIND_SESSION_END:
            self.blocks.append(
                ChatBlock(
                    "system",
                    text=(
                        f"Session ended: {data.get('reason', '')} ({data.get('turns', 0)} turns)"
                    ),
                ),
            )
            await self._render_chat()
        elif kind == KIND_ERROR:
            self.blocks.append(ChatBlock("error", text=str(data.get("message") or "Unknown error")))
            await self._render_chat()

    # ── sessions ────────────────────────────────────────────────────

    def _active_row(self) -> SessionRow | None:
        return next(
            (row for row in self.sessions if row.execution_id == self.active_session),
            None,
        )

    def _workflow_of(self, session_id: str) -> str | None:
        """The workflow a session runs under, or None when it is unknown.

        Never falls back to ``agent_chat``: an agent declaring a
        ``workflow_file`` runs under ``agent_custom_<name>``, so guessing
        here sends a resume (or a cancel) at a workflow the execution does
        not belong to, and the turn fails with nothing on screen explaining
        why. Callers report the unknown instead.
        """
        row = next((row for row in self.sessions if row.execution_id == session_id), None)
        if row is not None:
            return row.workflow_name
        if session_id == self.active_session:
            return self._active_workflow
        return None

    def _bucket_of(self, row: SessionRow) -> str:
        state = (row.state or "").upper()
        if state in ("FAILED", "CANCELLED"):
            return "failed"
        if state == "COMPLETED":
            return "done"
        if any(approval.execution_id == row.execution_id for approval in self.approvals):
            return "waiting"
        if state == "PAUSED":
            return "idle"
        if state in RUNNING_STATES:
            return "running"
        return "idle"

    def _session_title(self, row: SessionRow) -> str:
        if row.name:
            return row.name
        if row.derived_title:
            return row.derived_title
        cached = self.hub.titles.get(row.execution_id)
        if cached:
            return cached
        if row.execution_id == self.active_session and self._local_title:
            return self._local_title
        return f"{row.agent_name} · {row.execution_id[:8]}"

    async def open_session(self, session_id: str) -> None:
        """Render ``session_id``'s persisted log, then follow it live."""
        self.active_session = session_id
        self.plan = None
        self.subagents.clear()
        self._stream_text = ""
        self._reasoning = None
        self.blocks = []
        self.tools = {}
        detail = await self._read(self.hub.open_session(session_id), "session")
        self._active_workflow = detail.get("workflow_name") if detail else None
        if detail is not None:
            self.blocks, self.tools, self._local_title = derive_blocks(detail)
        await self._render_chat()
        await self._render_context()
        if self._agent_filter is not None and not any(
            row.execution_id == session_id for row in self.sessions
        ):
            # Opening a session the rail's filter excludes widens the rail
            # rather than leaving the session invisible in it.
            self._agent_filter = None
            await self._refresh_lists()
        # Focused explicitly: `r` and `x` act on the highlighted row, so the
        # highlight has to follow the session that is on screen.
        self._render_rail(focus=session_id)
        row = self._active_row()
        self.query_one("#chat-input", Input).placeholder = (
            f"Message {row.agent_name}…" if row else "Message…"
        )
        self._update_status()

    async def send_message_text(self, text: str) -> None:
        """Send one user turn on the open session and drive its events."""
        message = text.strip()
        if not message or self.active_session is None or self._busy:
            return
        if not self.can_write:
            required = self.missing_permission or "a write permission"
            self.notice = f"read-only: requires {required}"
            self._update_status()
            return
        workflow_name = self._workflow_of(self.active_session)
        if workflow_name is None:
            self.notice = f"send: unknown workflow for {self.active_session[:8]}"
            self._update_status()
            return
        row = self._active_row()
        agent_name = row.agent_name if row else (self.initial_agent or "")
        self.blocks.append(ChatBlock("user", text=message))
        self._busy = True
        await self._render_chat()
        self._update_status()
        try:
            # run_turn never raises: a broken stream is reported as an error
            # event and the turn still ends with its log reconciliation.
            await self.hub.run_turn(self.active_session, agent_name, workflow_name, message)
        finally:
            self._busy = False
            self._update_status()

    async def _create_session(self, agent_name: str, name: str) -> None:
        execution_id = await self._write(
            self.service.spawn(agent_name, name or None),
            "new session",
        )
        if execution_id and self._agent_filter not in (None, agent_name):
            # Started outside the initial filter: the rail has to show it.
            self._agent_filter = None
        await self._refresh_lists()
        if execution_id:
            await self.open_session(str(execution_id))

    async def _do_rename(self, session_id: str, name: str) -> None:
        await self._write(self.service.rename(session_id, name), "rename")
        await self._refresh_lists()

    async def decide_approval(
        self,
        approval: ApprovalRow,
        approve: bool,
        always: bool,
        always_for_target: bool,
    ) -> str:
        """Decide one approval gate and return the note for its row."""
        if not self.can_write:
            required = self.missing_permission or "a write permission"
            return f"read-only: requires {required}"
        result = await self._write(
            self.service.decide(
                approval.execution_id,
                approval.task_call_id,
                approve,
                always,
                always_for_target,
            ),
            "approval",
        )
        if result is None:
            return self.notice or "failed"
        await self._refresh_lists()
        if self.active_session == approval.execution_id:
            detail = await self._read(self.hub.open_session(approval.execution_id), "session")
            if detail is not None:
                self.blocks, self.tools, self._local_title = derive_blocks(detail)
                await self._render_chat()
        if result == "already_decided":
            return "already decided elsewhere"
        return "Approved." if approve else "Rejected."

    # ── rendering ───────────────────────────────────────────────────

    def _rail_prompt(self, row: SessionRow) -> Text:
        bucket = self._bucket_of(row)
        if bucket == "waiting":
            sub = "⚠ appr"
        elif bucket in ("idle", "done"):
            sub = bucket
        else:
            sub = (row.state or bucket).lower()[:9]
        age = _fmt_age(row.started_at)
        tail = f"{sub} {age}" if age else sub
        # One row, one line: a wrapped rail row reads as two sessions.
        title = _truncate(self._session_title(row), max(8, RAIL_TEXT_WIDTH - 3 - len(tail)))
        prompt = Text(no_wrap=True, overflow="ellipsis")
        prompt.append(GLYPH[bucket], style=GLYPH_STYLE[bucket])
        prompt.append(" ")
        prompt.append(title, style=MUTED if bucket == "done" else TEXT)
        pad = max(1, RAIL_TEXT_WIDTH - 2 - len(title) - len(tail))
        prompt.append(" " * pad)
        prompt.append(tail, style=AMBER if bucket == "waiting" else MUTED)
        return prompt

    def _render_rail(self, focus: str | None = None) -> None:
        rail = self.query_one("#panel-sessions", SessionRail)
        # Preference order: the session just opened, then whatever the
        # operator had highlighted, then the open session (a boot render has
        # no previous highlight to restore), then the first row.
        previous = focus or self._highlighted_session() or self.active_session

        # Failed gets its own heading: bucketing it under DONE would file a
        # session that needs attention with the ones that need none.
        groups: dict[str, list[SessionRow]] = {
            "ACTIVE": [],
            "IDLE": [],
            "DONE": [],
            "FAILED": [],
        }
        for row in self.sessions:
            bucket = self._bucket_of(row)
            if bucket in ("running", "waiting"):
                groups["ACTIVE"].append(row)
            elif bucket == "idle":
                groups["IDLE"].append(row)
            elif bucket == "done":
                groups["DONE"].append(row)
            else:
                groups["FAILED"].append(row)

        rail.clear_options()
        if not self.sessions:
            rail.add_option(
                Option(
                    Text("No sessions yet — press n for new session", style=MUTED),
                    disabled=True,
                ),
            )
        first_row_index: int | None = None
        restore_index: int | None = None
        index = 0
        for name, rows in groups.items():
            if not rows:
                continue
            # Headings ride along as disabled options so cursor movement
            # skips them and the rail needs no second widget.
            rail.add_option(Option(Text(name, style=f"bold {MUTED}"), disabled=True))
            index += 1
            for row in rows:
                rail.add_option(Option(self._rail_prompt(row), id=row.execution_id))
                if first_row_index is None:
                    first_row_index = index
                if row.execution_id == previous:
                    restore_index = index
                index += 1

        target = restore_index if restore_index is not None else first_row_index
        if target is not None:
            rail.highlighted = target

    def _highlighted_session(self) -> str | None:
        rail = self.query_one("#panel-sessions", SessionRail)
        index = rail.highlighted
        if index is None or index >= rail.option_count:
            return None
        return rail.get_option_at_index(index).id

    def _tool_title(self, tool: ToolCall) -> str:
        elapsed = ""
        if tool.ended is not None and tool.started is not None:
            elapsed = f"  {_fmt_elapsed(tool.ended - tool.started)}"
        mark = {"running": "⚙", "success": "✓"}.get(tool.status, "✗")
        return f"{mark} {tool.name}{elapsed}"

    def _block_widget(self, block: ChatBlock) -> Widget:
        if block.kind == "user":
            return Static(
                Text.assemble(("you  ", MUTED), (block.text, TEXT)),
                classes="chat-user",
            )
        if block.kind == "agent":
            return Static(
                Text.assemble(("agent  ", MUTED), (block.text, TEXT)),
                classes="chat-agent",
            )
        if block.kind == "tool" and block.tool is not None:
            body = Text()
            if block.tool.args:
                body.append(f"args {block.tool.args}\n", style=MUTED)
            if block.tool.output is not None:
                body.append(str(block.tool.output)[:2048], style=TEXT)
            return Collapsible(
                Static(body),
                title=self._tool_title(block.tool),
                collapsed=True,
            )
        if block.kind == "reasoning":
            return Collapsible(
                Static(Text(block.text, style=MUTED)),
                title="thinking",
                collapsed=True,
            )
        if block.kind == "approval":
            name = block.data.get("task_name") or "task"
            target = block.data.get("target_value")
            suffix = f" → {target}" if target else ""
            note = f" — {block.decided}" if block.decided else " — press a to decide"
            return Static(
                Text(f"⚠ approval required: {name}{suffix}{note}", style=AMBER),
                classes="chat-prompt",
            )
        if block.kind == "elicitation":
            server = block.data.get("server_name") or "server"
            message = block.data.get("message") or "Authorization required"
            note = f" — {block.decided}" if block.decided else " — press e to answer"
            return Static(
                Text(f"⚡ {server}: {message}{note}", style=AMBER),
                classes="chat-prompt",
            )
        if block.kind == "error":
            return Static(Text(f"✗ {block.text}", style=DANGER), classes="chat-error")
        return Static(Text(block.text, style=MUTED), classes="chat-system")

    def _stream_render(self) -> Text:
        return Text.assemble(("agent  ", MUTED), (self._stream_text, TEXT), ("▌", AMBER))

    async def _render_chat(self) -> None:
        log = self.query_one("#chat-log", VerticalScroll)
        following = log.scroll_y >= log.max_scroll_y - 2
        await log.remove_children()
        self._stream_widget = None
        if self.active_session is None:
            await log.mount(
                Static(
                    Text("No session open — press 1 to pick one, n for a new session", style=MUTED),
                    classes="chat-empty",
                ),
            )
            return
        widgets: list[Widget] = [self._block_widget(block) for block in self.blocks]
        if self._stream_text:
            self._stream_widget = Static(self._stream_render(), classes="chat-agent")
            widgets.append(self._stream_widget)
        elif self._busy:
            widgets.append(Static(Text("working…", style=MUTED), classes="chat-system"))
        if widgets:
            await log.mount_all(widgets)
        # Only follow the tail if the operator was already at it -- a
        # scrolled-back reader must not be yanked forward by every token.
        if following:
            log.scroll_end(animate=False)

    async def _render_stream(self) -> None:
        """Update the streaming reply in place (the per-token hot path)."""
        if self._stream_widget is None or not self._stream_widget.is_mounted:
            await self._render_chat()
            return
        log = self.query_one("#chat-log", VerticalScroll)
        following = log.scroll_y >= log.max_scroll_y - 2
        self._stream_widget.update(self._stream_render())
        if following:
            log.scroll_end(animate=False)

    def _plan_counts(self) -> tuple[int, int]:
        steps = (self.plan or {}).get("steps") or []
        done = sum(1 for step in steps if step.get("status") == "completed")
        return done, len(steps)

    async def _render_context(self) -> None:
        panel = self.query_one("#panel-context", ContextPanel)
        signature = (
            repr(self.plan),
            tuple((tool.id, tool.name, tool.status, tool.ended) for tool in self.tools.values()),
            tuple(repr(card) for card in self.subagents.values()),
        )
        # Rebuilding on every token would burn frames and throw away the
        # operator's place in the panel.
        if signature == self._context_signature:
            return
        self._context_signature = signature

        widgets: list[Widget] = [Static(Text("PLAN", style=MUTED), classes="section-label")]
        steps = (self.plan or {}).get("steps") or []
        if not steps:
            widgets.append(Static(Text("No plan yet", style=MUTED), classes="empty"))
        else:
            done, total = self._plan_counts()
            widgets.append(Static(Text(f"{done}/{total}", style=AMBER), classes="hero"))
            for step in steps:
                status = step.get("status") or "pending"
                # ● for in-progress rather than the web's ▶: a Collapsible
                # already wears an arrow, and two of them read as noise.
                mark = {"completed": "✓", "in_progress": "●", "failed": "✗"}.get(status, "○")
                body = Text()
                for key in ("description", "result", "error"):
                    if step.get(key):
                        body.append(f"{step[key]}\n", style=DANGER if key == "error" else MUTED)
                body.append(str(status).replace("_", " "), style=MUTED)
                widgets.append(
                    Collapsible(
                        Static(body),
                        title=f"{mark} {step.get('name', '')}",
                        collapsed=True,
                    ),
                )

        widgets.append(Static(Text("ACTIVITY", style=MUTED), classes="section-label"))
        if not self.tools:
            widgets.append(Static(Text("No tool calls yet", style=MUTED), classes="empty"))
        else:
            for tool in self.tools.values():
                widgets.append(
                    Static(
                        Text(
                            self._tool_title(tool),
                            style=AMBER if tool.status == "running" else MUTED,
                        ),
                    ),
                )

        widgets.append(Static(Text("SUB-AGENTS", style=MUTED), classes="section-label"))
        if not self.subagents:
            widgets.append(Static(Text("No sub-agents", style=MUTED), classes="empty"))
        else:
            for card in self.subagents.values():
                status = str(card.get("status") or "")
                mark = (
                    "✗"
                    if status == "failed"
                    else "◐"
                    if status == "paused"
                    else "●"
                    if status == "started"
                    else "○"
                )
                body = Text()
                if card.get("brief"):
                    body.append(f"{card['brief']}\n", style=MUTED)
                if card.get("result_tail"):
                    body.append(str(card["result_tail"]), style=TEXT)
                widgets.append(
                    Collapsible(
                        Static(body),
                        title=f"{mark} {card.get('agent') or card.get('id')} · {status}",
                        collapsed=status != "started",
                    ),
                )

        await panel.remove_children()
        await panel.mount_all(widgets)

    def _status_text(self) -> Text:
        pending = len(self.approvals)
        done, total = self._plan_counts()
        row = self._active_row()
        write_style = f"dim {MUTED}" if not self.can_write else AMBER
        status = Text()

        def hint(key: str, label: str, *, write: bool = False) -> None:
            status.append(key, style=write_style if write else AMBER)
            status.append(
                f" {label}  ",
                style=f"dim {MUTED}" if write and not self.can_write else MUTED,
            )

        if self._compact:
            hint("1/3", "panels")
            hint("n", "new", write=True)
            hint("a", "appr")
            hint("x", "stop", write=True)
            hint("^d", "quit")
        else:
            hint("1/2/3", "panels")
            hint("n", "new", write=True)
            hint("a", "approvals")
            # Only advertised while there is something to answer: an
            # always-on hint for a rare prompt spends width the session
            # identity needs.
            if self._pending_elicitation() is not None:
                hint("e", "answer", write=True)
            hint("r", "rename", write=True)
            hint("x", "stop", write=True)
            hint("enter", "open")
            hint("^d", "quit")
        status.append("│ ", style=LINE)

        # Below the chat-only width the line drops the session identity and
        # keeps only what a glance needs -- plan progress and pending
        # approvals -- rather than letting the terminal clip the tail off.
        tail: list[tuple[str, str]] = []
        if not self._minimal:
            if row is not None:
                tail.append((_truncate(self._session_title(row), 24), TEXT))
                tail.append(((row.state or "").lower(), MUTED))
            elif self.active_session:
                tail.append((self.active_session[:8], TEXT))
        if total:
            tail.append((f"step {done}/{total}", TEXT))
        tail.append((f"⚠{pending}", AMBER if pending else MUTED))
        if self._busy:
            tail.append(("working…", AMBER))
        for index, (text, style) in enumerate(tail):
            if index:
                status.append(" · ", style=LINE)
            status.append(text, style=style)
        if self.notice:
            status.append(f"  │ {self.notice}", style=DANGER)
        return status

    def _update_status(self) -> None:
        self.query_one("#status-line", Static).update(self._status_text())

    # ── layout ──────────────────────────────────────────────────────

    def _apply_layout(self, width: int) -> None:
        # Hints go compact as soon as the side panels fold away: the full
        # set no longer fits, and a clipped status line is worse than a
        # short one.
        self._compact = width < NARROW_WIDTH
        self._minimal = width < CHAT_ONLY_WIDTH
        self._hidden = {"sessions", "context"} if width < NARROW_WIDTH else set()
        self._sync_panels()

    def _sync_panels(self) -> None:
        for name, selector in _PANELS.items():
            self.query_one(selector).display = name not in self._hidden
        if self._focused_panel in self._hidden:
            self._focus_panel("chat")

    def on_resize(self, event: events.Resize) -> None:
        if not self._layout_ready:
            return
        # A resize is a fresh statement about how much room there is, so it
        # overrides whichever panels the operator toggled by hand.
        self._apply_layout(event.size.width)
        self._update_status()

    def _focus_panel(self, name: str) -> None:
        if name in self._hidden:
            return
        self._focused_panel = name
        # The chat panel focuses its transcript, not its composer: a focused
        # text field swallows every printable key, which would kill the
        # hotkeys that are the whole grammar. `enter` steps into the
        # composer, `escape` steps back out.
        self.query_one("#chat-log" if name == "chat" else _PANELS[name]).focus()
        self._mark_focused(name)

    def _mark_focused(self, name: str) -> None:
        for key, selector in _PANELS.items():
            self.query_one(selector).set_class(key == name, "-focused")

    def on_descendant_focus(self, event: events.DescendantFocus) -> None:
        node: Any = event.widget
        while node is not None:
            name = next((key for key, sel in _PANELS.items() if sel == f"#{node.id}"), None)
            if name is not None:
                self._focused_panel = name
                self._mark_focused(name)
                return
            node = node.parent

    # ── actions ─────────────────────────────────────────────────────

    def action_panel(self, name: str) -> None:
        if name in self._hidden:
            self._hidden.discard(name)
            self._sync_panels()
            self._focus_panel(name)
            return
        if name != "chat" and self._focused_panel == name and self.size.width < NARROW_WIDTH:
            # Only where columns are scarce does the key fold the panel you
            # are already on away again; at full width it would just be a
            # hotkey that hides things by accident.
            self._hidden.add(name)
            self._sync_panels()
            self._focus_panel("chat")
            return
        self._focus_panel(name)

    def action_compose(self) -> None:
        """Step into the composer (`enter` from the chat panel)."""
        if self._focused_panel != "chat":
            return
        self.query_one("#chat-input", Input).focus()

    def action_leave(self) -> None:
        """Step back out: composer → transcript → rail."""
        if self.query_one("#chat-input", Input).has_focus:
            self._focus_panel("chat")
            return
        self._focus_panel("sessions" if "sessions" not in self._hidden else "chat")

    def action_new_session(self) -> None:
        if not self.can_write:
            required = self.missing_permission or "a write permission"
            self.notice = f"read-only: requires {required}"
            self._update_status()
            return

        def started(result: tuple[str, str] | None) -> None:
            if result is not None:
                agent_name, name = result
                self.run_worker(self._create_session(agent_name, name), name="console-new")

        self.push_screen(NewSessionScreen(self.agents), started)

    def action_approvals(self) -> None:
        self.push_screen(ApprovalsScreen(self.approvals, self.can_write, self.decide_approval))

    def action_elicitation(self) -> None:
        """Answer the open session's outstanding elicitation.

        Without this the terminal console can see an MCP authorization
        prompt but never answer it, so any flow that raises one hangs until
        the operator goes to the web console.
        """
        block = self._pending_elicitation()
        if block is None:
            self.notice = "no elicitation to answer"
            self._update_status()
            return
        if not self.can_write:
            required = self.missing_permission or "a write permission"
            self.notice = f"read-only: requires {required}"
            self._update_status()
            return

        def answered(action: str | None) -> None:
            if action:
                self.run_worker(
                    self._respond_to_elicitation(block, action),
                    name="console-elicitation",
                )

        self.push_screen(ElicitationScreen(block.data, self.can_write), answered)

    def _pending_elicitation(self) -> ChatBlock | None:
        """The open session's most recent unanswered elicitation, if any."""
        for block in reversed(self.blocks):
            if block.kind == "elicitation" and not block.decided:
                return block
        return None

    async def _respond_to_elicitation(self, block: ChatBlock, action: str) -> None:
        session_id = block.session_id or self.active_session
        if session_id is None:
            return
        workflow_name = self._workflow_of(session_id)
        if workflow_name is None:
            self.notice = f"elicitation: unknown workflow for {session_id[:8]}"
            self._update_status()
            return
        payload = {
            "elicitation_response": {
                "elicitation_id": block.data.get("elicitation_id", ""),
                "action": action,
            },
        }
        previous = block.decided
        block.decided = action
        await self._render_chat()
        try:
            # Called straight on the service: this console is in-process, so
            # the /console/sessions/{id}/elicitation route the web app posts
            # to is this same call, one HTTP hop further away.
            await self.service.respond_to_elicitation(session_id, workflow_name, payload)
        except Exception as exc:
            # The prompt is still outstanding, so the block goes back to
            # unanswered and `e` can try again.
            block.decided = previous
            self._note_failure(exc, "elicitation", is_write=True)
            await self._render_chat()
            return
        await self._refresh_lists()

    def action_stop_session(self) -> None:
        """Cancel the highlighted (or open) session, on a second press."""
        session_id = self._highlighted_session() or self.active_session
        if session_id is None:
            self.notice = "stop: no session highlighted"
            self._update_status()
            return
        if not self.can_write:
            required = self.missing_permission or "a write permission"
            self.notice = f"read-only: requires {required}"
            self._update_status()
            return
        row = next(
            (candidate for candidate in self.sessions if candidate.execution_id == session_id),
            None,
        )
        if row is not None and (row.state or "").upper() in TERMINAL_STATES:
            self.notice = "stop: session already finished"
            self._update_status()
            return
        workflow_name = self._workflow_of(session_id)
        if workflow_name is None:
            self.notice = f"stop: unknown workflow for {session_id[:8]}"
            self._update_status()
            return
        if self._stop_armed != session_id:
            # Armed, not fired: a cancel is not undoable, and `x` is one key
            # away from every other single-press action.
            self._stop_armed = session_id
            title = self._session_title(row) if row is not None else session_id[:8]
            self.notice = f"press x again to stop {title}"
            self._update_status()
            return
        self._stop_armed = None
        self.run_worker(self._stop_session(session_id, workflow_name), name="console-stop")

    async def _stop_session(self, session_id: str, workflow_name: str) -> None:
        # Console sessions always live in the `agents` namespace (the same
        # one ConsoleService.spawn starts them in).
        await self._write(self.service.stop(session_id, "agents", workflow_name), "stop")
        await self._refresh_lists()
        if self.active_session == session_id:
            await self.open_session(session_id)

    def action_rename(self) -> None:
        session_id = self._highlighted_session() or self.active_session
        if session_id is None:
            self.notice = "rename: no session highlighted"
            self._update_status()
            return
        if not self.can_write:
            required = self.missing_permission or "a write permission"
            self.notice = f"read-only: requires {required}"
            self._update_status()
            return
        row = next((row for row in self.sessions if row.execution_id == session_id), None)
        current = row.name if row and row.name else ""

        def renamed(name: str | None) -> None:
            if name:
                self.run_worker(self._do_rename(session_id, name), name="console-rename")

        self.push_screen(RenameScreen(current), renamed)

    def action_quit_console(self) -> None:
        self.exit()

    # ── input ───────────────────────────────────────────────────────

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "chat-input":
            return
        text = event.value.strip()
        event.input.clear()
        if text:
            self.run_worker(self.send_message_text(text), name="console-turn")

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id != "panel-sessions":
            return
        session_id = event.option.id
        if session_id:
            self.run_worker(self.open_session(session_id), name="console-open")
