"""Overlay screens for the Textual mission control.

Four modal overlays sit on top of the three-panel console: starting a
session, deciding pending approvals, answering an elicitation, and renaming
a session. They are modal rather than inline panels because each one takes
over the keyboard -- in a terminal there is no second place to put a
focused control without stealing keys from the panel underneath.

Every screen is a pure renderer of what it is handed: the app owns the
service calls (and the read-only degradation that comes with them), so a
screen never learns about HTTP, and the decision callback it invokes
returns the note to show on the row.
"""

from __future__ import annotations

import webbrowser
from collections.abc import Awaitable, Callable

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, OptionList, Static
from textual.widgets.option_list import Option

from flux.agents.console.service import ApprovalRow
from flux.tasks.mcp.elicitation import is_browsable

# Kept in sync with textual_app's palette; imported from there would be a
# cycle (the app imports the screens), and both trace back to the design's
# dark tokens.
_AMBER = "#f0a828"
_PANEL = "#0e1524"
_LINE = "#1c2536"
_MUTED = "#8b96a8"

DecideCallback = Callable[[ApprovalRow, bool, bool, bool], Awaitable[str]]


class NewSessionScreen(ModalScreen[tuple[str, str] | None]):
    """Pick an agent (filterable) and optionally name the session."""

    BINDINGS = [Binding("escape", "cancel", "cancel", show=False)]

    DEFAULT_CSS = f"""
    NewSessionScreen {{
        align: center middle;
        background: #0b111c 70%;
    }}
    NewSessionScreen #overlay {{
        width: 56;
        height: auto;
        max-height: 80%;
        background: {_PANEL};
        border: solid {_AMBER};
        border-title-color: {_AMBER};
        border-title-align: left;
        padding: 0 1;
    }}
    NewSessionScreen #agent-list {{
        height: auto;
        max-height: 10;
        background: {_PANEL};
        border: none;
    }}
    NewSessionScreen Input {{
        background: {_PANEL};
        border: hkey {_LINE};
    }}
    NewSessionScreen Input:focus {{
        border: hkey {_AMBER};
    }}
    NewSessionScreen .overlay-hint {{
        color: {_MUTED};
    }}
    """

    def __init__(self, agents: list[str]) -> None:
        super().__init__()
        self._agents = list(agents)
        self._selected: str | None = self._agents[0] if self._agents else None

    def compose(self) -> ComposeResult:
        with Vertical(id="overlay") as overlay:
            overlay.border_title = "new session"
            yield Input(placeholder="filter agents…", id="agent-filter")
            yield OptionList(id="agent-list")
            yield Input(placeholder="name (optional)", id="session-name")
            yield Static("enter start · esc cancel", classes="overlay-hint")

    def on_mount(self) -> None:
        self._populate("")
        self.query_one("#agent-filter", Input).focus()

    def _populate(self, needle: str) -> None:
        options = self.query_one("#agent-list", OptionList)
        options.clear_options()
        matches = [name for name in self._agents if needle.lower() in name.lower()]
        for name in matches:
            options.add_option(Option(name, id=name))
        self._selected = matches[0] if matches else None
        if matches:
            options.highlighted = 0

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "agent-filter":
            self._populate(event.value)

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        self._selected = event.option.id or str(event.option.prompt)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self._selected = event.option.id or str(event.option.prompt)
        self.query_one("#session-name", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._start()

    def _start(self) -> None:
        if self._selected is None:
            return
        name = self.query_one("#session-name", Input).value.strip()
        self.dismiss((self._selected, name))

    def action_cancel(self) -> None:
        self.dismiss(None)


class RenameScreen(ModalScreen[str | None]):
    """Rename the highlighted session."""

    BINDINGS = [Binding("escape", "cancel", "cancel", show=False)]

    DEFAULT_CSS = f"""
    RenameScreen {{
        align: center middle;
        background: #0b111c 70%;
    }}
    RenameScreen #overlay {{
        width: 56;
        height: auto;
        background: {_PANEL};
        border: solid {_AMBER};
        border-title-color: {_AMBER};
        border-title-align: left;
        padding: 0 1;
    }}
    RenameScreen Input {{
        background: {_PANEL};
        border: hkey {_LINE};
    }}
    RenameScreen Input:focus {{
        border: hkey {_AMBER};
    }}
    RenameScreen .overlay-hint {{
        color: {_MUTED};
    }}
    """

    def __init__(self, current: str = "") -> None:
        super().__init__()
        self._current = current

    def compose(self) -> ComposeResult:
        with Vertical(id="overlay") as overlay:
            overlay.border_title = "rename session"
            yield Input(value=self._current, placeholder="session name", id="rename-input")
            yield Static("enter save · esc cancel", classes="overlay-hint")

    def on_mount(self) -> None:
        field = self.query_one("#rename-input", Input)
        field.focus()
        field.cursor_position = len(field.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        name = event.value.strip()
        self.dismiss(name or None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ElicitationScreen(ModalScreen[str | None]):
    """Answer one MCP elicitation: accept, decline, or cancel.

    An overlay rather than buttons on the chat block, for the same reason
    the other actions are overlays: the transcript is re-rendered on every
    streamed event, which would remount inline controls under the
    operator's fingers.

    The url is opened only by an explicit press, and only when it is
    absolute http(s) -- the string comes from a remote MCP server, so a
    ``file:``/``javascript:`` payload must never reach ``webbrowser``.
    """

    BINDINGS = [
        Binding("escape", "cancel", "cancel", show=False),
        Binding("y", "accept", "accept", show=False),
        Binding("n", "decline", "decline", show=False),
    ]

    DEFAULT_CSS = f"""
    ElicitationScreen {{
        align: center middle;
        background: #0b111c 70%;
    }}
    ElicitationScreen #overlay {{
        width: 64;
        height: auto;
        max-height: 80%;
        background: {_PANEL};
        border: solid {_AMBER};
        border-title-color: {_AMBER};
        border-title-align: left;
        padding: 0 1;
    }}
    ElicitationScreen .elicitation-actions {{
        /* Without this the button row is a 1fr Horizontal and stretches to
           fill the overlay, pushing the key hint to the bottom edge. Same
           rule ApprovalsScreen's action row carries. */
        height: auto;
    }}
    ElicitationScreen .elicitation-message {{
        color: {_AMBER};
    }}
    ElicitationScreen .elicitation-url, ElicitationScreen .overlay-hint {{
        color: {_MUTED};
    }}
    ElicitationScreen Button {{
        min-width: 8;
        margin: 0 1 0 0;
        background: {_LINE};
        color: #c9d2e0;
    }}
    ElicitationScreen Button:focus {{
        text-style: bold;
        background: {_AMBER};
        color: #0b111c;
    }}
    """

    def __init__(self, request: dict, can_write: bool) -> None:
        super().__init__()
        self._request = dict(request or {})
        self._can_write = can_write

    @property
    def _url(self) -> str:
        url = self._request.get("url")
        return url if isinstance(url, str) else ""

    def compose(self) -> ComposeResult:
        server = self._request.get("server_name") or "server"
        message = self._request.get("message") or "Authorization required"
        with Vertical(id="overlay") as overlay:
            overlay.border_title = "authorization"
            yield Static(f"⚡ {server}: {message}", classes="elicitation-message")
            if self._url:
                yield Static(
                    self._url
                    if is_browsable(self._url)
                    else f"refusing a non-browsable url: {self._url}",
                    classes="elicitation-url",
                )
            with Horizontal(classes="elicitation-actions"):
                yield Button("Accept", id="accept", compact=True, disabled=not self._can_write)
                yield Button("Decline", id="decline", compact=True, disabled=not self._can_write)
                yield Button("Cancel", id="cancel", compact=True, disabled=not self._can_write)
                if self._url and is_browsable(self._url):
                    yield Button("Open url", id="open-url", compact=True)
            yield Static("y accept · n decline · esc close", classes="overlay-hint")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        action = event.button.id or ""
        if action == "open-url":
            # Explicit operator action, never automatic, and only for an
            # address already proven to be http(s).
            if is_browsable(self._url):
                webbrowser.open(self._url)
            return
        self.dismiss(action or None)

    def action_accept(self) -> None:
        if self._can_write:
            self.dismiss("accept")

    def action_decline(self) -> None:
        if self._can_write:
            self.dismiss("decline")

    def action_cancel(self) -> None:
        self.dismiss(None)


class ApprovalsScreen(ModalScreen[None]):
    """Pending approvals, one row per gate, with the four decisions.

    ``Always for target`` only appears for a gate that declared an
    ``approval_target``: without one there is nothing to scope the standing
    grant to, and offering it would promise a narrower grant than the
    engine can honour.
    """

    BINDINGS = [Binding("escape", "cancel", "cancel", show=False)]

    DEFAULT_CSS = f"""
    ApprovalsScreen {{
        align: center middle;
        background: #0b111c 70%;
    }}
    ApprovalsScreen #overlay {{
        width: 72;
        height: auto;
        max-height: 90%;
        background: {_PANEL};
        border: solid {_AMBER};
        border-title-color: {_AMBER};
        border-title-align: left;
        padding: 0 1;
    }}
    ApprovalsScreen #approval-rows {{
        height: auto;
        max-height: 24;
    }}
    ApprovalsScreen .approval-row {{
        height: auto;
        border-bottom: solid {_LINE};
        padding: 0 0 1 0;
    }}
    ApprovalsScreen .approval-actions {{
        height: auto;
    }}
    ApprovalsScreen Button {{
        min-width: 8;
        margin: 0 1 0 0;
        background: {_LINE};
        color: #c9d2e0;
    }}
    ApprovalsScreen Button:focus {{
        text-style: bold;
        background: {_AMBER};
        color: #0b111c;
    }}
    ApprovalsScreen .approval-meta, ApprovalsScreen .overlay-hint {{
        color: {_MUTED};
    }}
    ApprovalsScreen .approval-note {{
        color: {_AMBER};
    }}
    """

    def __init__(
        self,
        approvals: list[ApprovalRow],
        can_write: bool,
        decide: DecideCallback,
    ) -> None:
        super().__init__()
        self._approvals = list(approvals)
        self._can_write = can_write
        self._decide = decide

    def compose(self) -> ComposeResult:
        with Vertical(id="overlay") as overlay:
            overlay.border_title = f"approvals ({len(self._approvals)})"
            with VerticalScroll(id="approval-rows"):
                if not self._approvals:
                    yield Static("No pending approvals", classes="approval-meta")
                for index, approval in enumerate(self._approvals):
                    yield from self._row(index, approval)
            yield Static("esc close", classes="overlay-hint")

    def _row(self, index: int, approval: ApprovalRow) -> ComposeResult:
        with Vertical(id=f"row-{index}", classes="approval-row"):
            yield Static(approval.task_name, classes="approval-task")
            if approval.target_value:
                yield Static(f"→ {approval.target_value}", classes="approval-meta")
            yield Static(
                f"{approval.execution_id[:8]} · {approval.requested_at or 'just now'}",
                classes="approval-meta",
            )
            with Horizontal(classes="approval-actions"):
                # compact: a bordered button is three rows tall, which turns
                # a handful of pending gates into a scrolling wall.
                yield Button(
                    "Approve",
                    id=f"approve-{index}",
                    compact=True,
                    disabled=not self._can_write,
                )
                yield Button(
                    "Reject",
                    id=f"reject-{index}",
                    compact=True,
                    disabled=not self._can_write,
                )
                yield Button(
                    "Always",
                    id=f"always-{index}",
                    compact=True,
                    disabled=not self._can_write,
                )
                if approval.target_value:
                    yield Button(
                        "Always for target",
                        id=f"always-target-{index}",
                        compact=True,
                        disabled=not self._can_write,
                    )
            yield Static("", id=f"note-{index}", classes="approval-note")

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        kind, _, index_text = button_id.rpartition("-")
        if not index_text.isdigit():
            return
        index = int(index_text)
        approval = self._approvals[index]
        note = await self._decide(
            approval,
            kind != "reject",
            kind == "always",
            kind == "always-target",
        )
        self.query_one(f"#note-{index}", Static).update(note)
        # A decided gate cannot be decided again; leaving the buttons live
        # would invite a second POST that can only come back already_decided.
        for button in self.query_one(f"#row-{index}").query(Button):
            button.disabled = True

    def action_cancel(self) -> None:
        self.dismiss(None)
