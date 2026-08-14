"""The console's three panel widgets.

Panels rather than generic blocks: the transcript, the plan and the tool
activity are all rendered by ``ConsoleApp`` itself (from ``ChatBlock`` /
``ToolCall``), so what lives here is only the structure those renders mount
into, plus the border titles that carry each panel's hotkey.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Input, OptionList


class SessionRail(OptionList):
    """Console panel 1 — the session rail.

    An ``OptionList`` rather than a hand-rolled list because the rail needs
    exactly what it already gives: a moving highlight (which `r` renames and
    `enter` opens) and group headings, which ride along as disabled options
    so cursor movement skips them.
    """

    BORDER_TITLE = "1sessions"


class ChatPanel(Vertical):
    """Console panel 2 — transcript above, composer docked below."""

    BORDER_TITLE = "2chat"

    def compose(self) -> ComposeResult:
        yield VerticalScroll(id="chat-log")
        yield Input(placeholder="Message…", id="chat-input")


class ContextPanel(VerticalScroll):
    """Console panel 3 — plan, activity, sub-agents."""

    BORDER_TITLE = "3context"
