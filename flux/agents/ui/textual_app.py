"""Textual App for the agent terminal UI."""

from __future__ import annotations

import asyncio
import time
import webbrowser

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widgets import Input, Markdown, OptionList, Static

from flux.agents.ui.textual_messages import (
    ElicitationRequested,
    ReasoningReceived,
    ReplyEnded,
    ReplyStarted,
    ResponseReceived,
    SessionEnded,
    SessionInfoReceived,
    TokenReceived,
    ToolCompleted,
    ToolStarted,
)
from flux.agents.ui.textual_widgets import (
    ElicitationPrompt,
    SpinnerBlock,
    StreamBlock,
    ThinkingBlock,
    ToolBlock,
)

QUIT_SENTINEL = "\x04"

_SLASH_COMMANDS = {
    "/help": "Show available commands",
    "/session": "Show session ID",
    "/clear": "Clear chat history",
    "/quit": "End session",
}


class AgentApp(App):
    """Full-screen agent chat TUI."""

    BINDINGS = [
        Binding("ctrl+d", "quit_app", "Exit", show=False),
    ]

    CSS = """
    Screen {
        background: $background;
        layers: default above;
    }

    #chat-view {
        scrollbar-size: 1 1;
        scrollbar-background: $background;
        scrollbar-color: $surface-lighten-2;
        scrollbar-color-hover: $surface-lighten-3;
        scrollbar-color-active: $accent;
        min-height: 1;
        margin: 0;
        padding: 0;
    }

    .user-message {
        height: auto;
        padding: 0 1;
        color: $text;
        background: transparent;
    }

    .system-message {
        height: auto;
        padding: 0 1;
        color: $text-muted;
        background: transparent;
    }

    StreamBlock {
        background: transparent;
    }

    StreamBlock Markdown {
        background: transparent;
        margin: 0;
        padding: 0 1;
    }

    ToolBlock {
        background: transparent;
    }

    Collapsible {
        background: transparent;
        border: none;
        padding: 0 1;
    }

    Collapsible > Contents {
        background: transparent;
    }

    CollapsibleTitle {
        background: transparent;
        color: $text-muted;
        padding: 0;
    }

    #status-bar {
        dock: bottom;
        height: 1;
        background: transparent;
        color: $text-muted;
        padding: 0 1;
    }

    #agent-input {
        dock: bottom;
        height: 3;
        margin: 1 0 2 0;
        border: hkey $surface-lighten-1;
        background: $background;
        padding: 0 1;
    }

    #agent-input:focus {
        border: hkey $accent;
    }

    #slash-completions {
        dock: bottom;
        layer: above;
        height: auto;
        max-height: 6;
        display: none;
        background: $surface;
        border: tall $accent;
        margin-bottom: 6;
    }
    """

    def __init__(
        self,
        input_queue: asyncio.Queue[str],
        user_id: str | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._input_queue = input_queue
        self._user_id: str | None = user_id
        self._session_id: str | None = None
        self._agent_name: str = ""
        self._current_stream: StreamBlock | None = None
        self._current_thinking: ThinkingBlock | None = None
        self._pending_tools: dict[str, ToolBlock] = {}
        self._history: list[str] = []
        self._history_index: int = -1
        self._navigating_history: bool = False
        self._elicitation_future: asyncio.Future | None = None
        self._elicitation_url: str = ""
        self._turn_count: int = 0
        self._session_start: float = time.monotonic()
        self._is_processing: bool = False
        self._spinner: SpinnerBlock | None = None

    def compose(self) -> ComposeResult:
        yield VerticalScroll(id="chat-view")
        yield OptionList(id="slash-completions")
        yield Input(placeholder="Send a message…", id="agent-input")
        yield Static(self._build_status(), id="status-bar")

    def on_mount(self) -> None:
        self.query_one("#agent-input", Input).focus()

    def on_unmount(self) -> None:
        if self._elicitation_future and not self._elicitation_future.done():
            self._elicitation_future.cancel()
        self._is_processing = False
        if self._spinner is not None:
            self._spinner.stop()
            self._spinner = None
        self._input_queue.put_nowait(QUIT_SENTINEL)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        event.input.clear()
        self.query_one("#slash-completions", OptionList).display = False

        if not text:
            return

        self._history.append(text)
        self._history_index = -1

        if text.startswith("/"):
            self._handle_slash_command(text)
        else:
            self._append_user_message(text)
            self._input_queue.put_nowait(text)

    def on_input_changed(self, event: Input.Changed) -> None:
        if self._navigating_history:
            return
        self._history_index = -1
        text = event.value
        completions = self.query_one("#slash-completions", OptionList)

        if text.startswith("/"):
            prefix = text.strip()
            matches = [
                (cmd, desc) for cmd, desc in _SLASH_COMMANDS.items() if cmd.startswith(prefix)
            ]
            completions.clear_options()
            for cmd, desc in matches:
                completions.add_option(f"{cmd}  — {desc}")
            if matches:
                completions.display = True
                completions.highlighted = 0
            else:
                completions.display = False
        else:
            completions.display = False

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        label = str(event.option.prompt)
        command = label.split("  —")[0].strip()
        self.query_one("#agent-input", Input).clear()
        self.query_one("#slash-completions", OptionList).display = False
        self._handle_slash_command(command)

    def _handle_slash_command(self, command: str) -> None:
        chat = self.query_one("#chat-view", VerticalScroll)
        if command == "/quit":
            self._input_queue.put_nowait(QUIT_SENTINEL)
            self.exit()
        elif command == "/help":
            lines = "\n".join(f"  {k}  {v}" for k, v in _SLASH_COMMANDS.items())
            chat.mount(Static(lines, classes="system-message"))
            self._auto_scroll()
        elif command == "/session":
            sid = self._session_id or "not connected"
            chat.mount(Static(f"  session {sid}", classes="system-message"))
            self._auto_scroll()
        elif command == "/clear":
            chat.remove_children()
            self._current_stream = None
            self._current_thinking = None
            self._pending_tools.clear()

    def _append_user_message(self, text: str) -> None:
        self._turn_count += 1
        chat = self.query_one("#chat-view", VerticalScroll)
        chat.mount(Static(f"❯ {text}", classes="user-message"))
        self._auto_scroll()

    def on_click(self, event) -> None:
        self.query_one("#agent-input", Input).focus()

    async def on_key(self, event) -> None:
        if event.key == "ctrl+d":
            self.action_quit_app()
            event.prevent_default()
            return

        input_widget = self.query_one("#agent-input", Input)

        if self._elicitation_future is not None and not self._elicitation_future.done():
            if event.key in ("y", "Y"):
                if self._elicitation_url:
                    webbrowser.open(self._elicitation_url)
                self._elicitation_future.set_result("accept")
                self._elicitation_future = None
                self._update_status_bar()
                event.prevent_default()
                return
            if event.key in ("n", "N"):
                self._elicitation_future.set_result("decline")
                self._elicitation_future = None
                self._update_status_bar()
                event.prevent_default()
                return

        completions = self.query_one("#slash-completions", OptionList)

        if event.key == "tab" and completions.display:
            idx = completions.highlighted
            if idx is not None and idx < completions.option_count:
                label = str(completions.get_option_at_index(idx).prompt)
                command = label.split("  —")[0].strip()
                input_widget.value = command
                input_widget.cursor_position = len(command)
                completions.display = False
            event.prevent_default()
            return

        if completions.display and event.key in ("up", "down"):
            if event.key == "up" and completions.highlighted is not None:
                completions.highlighted = max(0, completions.highlighted - 1)
            elif event.key == "down" and completions.highlighted is not None:
                completions.highlighted = min(
                    completions.option_count - 1,
                    completions.highlighted + 1,
                )
            event.prevent_default()
            return

        if event.key == "escape" and input_widget.has_focus:
            input_widget.clear()
            completions.display = False
            event.prevent_default()
            return

        if event.key == "up" and input_widget.has_focus:
            if self._history and (input_widget.value == "" or self._history_index != -1):
                if self._history_index == -1:
                    self._history_index = len(self._history) - 1
                elif self._history_index > 0:
                    self._history_index -= 1
                self._navigating_history = True
                input_widget.value = self._history[self._history_index]
                input_widget.cursor_position = len(input_widget.value)
                self._navigating_history = False
            event.prevent_default()
            return

        if event.key == "down" and input_widget.has_focus and self._history_index != -1:
            self._navigating_history = True
            if self._history_index < len(self._history) - 1:
                self._history_index += 1
                input_widget.value = self._history[self._history_index]
                input_widget.cursor_position = len(input_widget.value)
            else:
                self._history_index = -1
                input_widget.clear()
            self._navigating_history = False
            event.prevent_default()
            return

    def action_quit_app(self) -> None:
        self._input_queue.put_nowait(QUIT_SENTINEL)
        self.exit()

    # ── Spinner ─────────────────────────────────────────────────────

    def _start_spinner(self, label: str = "Thinking") -> None:
        self._is_processing = True
        if self._spinner is None:
            chat = self.query_one("#chat-view", VerticalScroll)
            self._spinner = SpinnerBlock(label)
            chat.mount(self._spinner)
            self._auto_scroll()
        else:
            self._spinner.set_label(label)

    async def _stop_spinner(self) -> None:
        self._is_processing = False
        if self._spinner is not None:
            self._spinner.stop()
            await self._spinner.remove()
            self._spinner = None

    # ── Event handlers ──────────────────────────────────────────────

    def on_session_info_received(self, message: SessionInfoReceived) -> None:
        self._session_id = message.session_id
        self._agent_name = message.agent_name
        self._update_status_bar()

    def on_reply_started(self, message: ReplyStarted) -> None:
        self._start_spinner("Thinking")

    async def on_reply_ended(self, message: ReplyEnded) -> None:
        self._finalize_current_thinking()
        if self._current_stream is not None:
            await self._current_stream.finalize()
            self._current_stream = None
        await self._stop_spinner()
        self._update_status_bar()
        self.query_one("#agent-input", Input).focus()

    async def on_token_received(self, message: TokenReceived) -> None:
        self._finalize_current_thinking()
        chat = self.query_one("#chat-view", VerticalScroll)
        if self._current_stream is None:
            self._current_stream = StreamBlock()
            chat.mount(self._current_stream)
        self._current_stream.append_token(message.text)
        if self._spinner is not None:
            self._spinner.set_label("Streaming")
        self._auto_scroll()

    async def on_reasoning_received(self, message: ReasoningReceived) -> None:
        if self._spinner is not None:
            await self._stop_spinner()
        chat = self.query_one("#chat-view", VerticalScroll)
        if self._current_thinking is None:
            self._current_thinking = ThinkingBlock()
            chat.mount(self._current_thinking)
        self._current_thinking.append_text(message.text)
        self._auto_scroll()

    async def on_tool_started(self, message: ToolStarted) -> None:
        self._finalize_current_thinking()
        if self._spinner is not None:
            await self._stop_spinner()
        chat = self.query_one("#chat-view", VerticalScroll)
        block = ToolBlock(message.name, message.args)
        self._pending_tools[message.tool_id] = block
        chat.mount(block)
        self._auto_scroll()

    def on_tool_completed(self, message: ToolCompleted) -> None:
        block = self._pending_tools.pop(message.tool_id, None)
        if block:
            block.mark_done(message.status)

    async def on_response_received(self, message: ResponseReceived) -> None:
        if self._current_stream is not None:
            await self._current_stream.finalize(message.content)
            self._current_stream = None
        elif message.content:
            chat = self.query_one("#chat-view", VerticalScroll)
            await chat.mount(Markdown(message.content))
        self._auto_scroll()

    async def on_elicitation_requested(self, message: ElicitationRequested) -> None:
        chat = self.query_one("#chat-view", VerticalScroll)
        prompt = ElicitationPrompt(
            server_name=message.request.get("server_name", "unknown"),
            message=message.request.get("message", "Authorization required"),
        )
        chat.mount(prompt)
        self._auto_scroll()
        self._elicitation_future = message.future
        self._elicitation_url = message.request.get("url", "")
        await self._stop_spinner()
        status = self.query_one("#status-bar", Static)
        status.update("  [Y]es to authorize  │  [N]o to decline")

    async def on_session_ended(self, message: SessionEnded) -> None:
        chat = self.query_one("#chat-view", VerticalScroll)
        chat.mount(
            Static(
                f"  Session ended: {message.reason} ({message.turns} turns)",
                classes="system-message",
            ),
        )
        await self._stop_spinner()
        self._auto_scroll()

    # ── Helpers ──────────────────────────────────────────────────────

    def _finalize_current_thinking(self) -> None:
        if self._current_thinking is not None:
            self._current_thinking.finalize()
            self._current_thinking = None

    def _build_status(self) -> str:
        elapsed = time.monotonic() - self._session_start
        minutes = int(elapsed // 60)
        agent = self._agent_name or "connecting"
        turns = f"{self._turn_count} turn{'s' if self._turn_count != 1 else ''}"
        duration = f"{minutes}m" if minutes > 0 else "<1m"
        sid = f"  │  {self._session_id}" if self._session_id else ""
        user = f"  │  {self._user_id}" if self._user_id else ""
        return f"  {agent}{sid}  │  {turns}  │  {duration}{user}  │  /help  │  Ctrl+D quit"

    def _update_status_bar(self) -> None:
        status = self.query_one("#status-bar", Static)
        status.update(self._build_status())

    def _auto_scroll(self) -> None:
        chat = self.query_one("#chat-view", VerticalScroll)
        if chat.scroll_y >= chat.max_scroll_y - 2:
            chat.scroll_end(animate=False)
