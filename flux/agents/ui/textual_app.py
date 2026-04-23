"""Textual App for the agent terminal UI."""

from __future__ import annotations

import asyncio
import webbrowser

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widgets import Markdown, OptionList, Static, TextArea

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
    StreamBlock,
    ThinkingBlock,
    ToolBlock,
)

QUIT_SENTINEL = "\x04"

_DEFAULT_STATUS = "/help commands  │  Enter send  │  Shift+Enter newline  │  Ctrl+D exit"

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
    #agent-header {
        dock: top;
        height: 1;
        background: $surface;
        color: $text-muted;
        padding: 0 1;
    }

    #chat-view {
        scrollbar-gutter: stable;
        min-height: 1;
    }

    .user-message {
        height: auto;
        padding: 0 1;
        color: $text;
        margin: 1 0 0 0;
    }

    .user-label {
        height: auto;
        padding: 0 1;
        color: $accent;
        text-style: bold;
    }

    .agent-label {
        height: auto;
        padding: 0 1;
        color: $success;
        text-style: bold;
    }

    #agent-input {
        dock: bottom;
        height: 3;
        border: tall $accent;
    }

    #agent-input:disabled {
        border: tall $surface;
        opacity: 0.5;
    }

    #status-bar {
        dock: bottom;
        height: 1;
        background: $surface;
        color: $text-muted;
        padding: 0 1;
    }

    #slash-completions {
        dock: bottom;
        height: auto;
        max-height: 6;
        display: none;
        background: $surface;
        border: tall $accent;
    }
    """

    def __init__(self, input_queue: asyncio.Queue[str], **kwargs) -> None:
        super().__init__(**kwargs)
        self._input_queue = input_queue
        self._session_id: str | None = None
        self._agent_name: str = ""
        self._current_stream: StreamBlock | None = None
        self._current_thinking: ThinkingBlock | None = None
        self._pending_tools: list[ToolBlock] = []
        self._agent_label_shown: bool = False
        self._history: list[str] = []
        self._history_index: int = -1
        self._elicitation_future: asyncio.Future | None = None
        self._elicitation_url: str = ""

    def compose(self) -> ComposeResult:
        yield Static("Connecting...", id="agent-header")
        yield VerticalScroll(id="chat-view")
        yield Static(_DEFAULT_STATUS, id="status-bar")
        yield OptionList(id="slash-completions")
        yield TextArea(id="agent-input")

    def on_mount(self) -> None:
        input_widget = self.query_one("#agent-input", TextArea)
        input_widget.focus()

    def on_unmount(self) -> None:
        if self._elicitation_future and not self._elicitation_future.done():
            self._elicitation_future.cancel()
        self._input_queue.put_nowait(QUIT_SENTINEL)

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        self._history_index = -1
        text = event.text_area.text
        completions = self.query_one("#slash-completions", OptionList)
        if text.startswith("/") and not text.endswith("\n"):
            prefix = text.strip()
            matches = [
                (cmd, desc) for cmd, desc in _SLASH_COMMANDS.items() if cmd.startswith(prefix)
            ]
            completions.clear_options()
            for cmd, desc in matches:
                completions.add_option(f"{cmd}  — {desc}")
            completions.display = bool(matches)
        else:
            completions.display = False

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        label = str(event.option.prompt)
        command = label.split("  —")[0].strip()
        input_widget = self.query_one("#agent-input", TextArea)
        input_widget.clear()
        completions = self.query_one("#slash-completions", OptionList)
        completions.display = False
        self._handle_slash_command(command)

    def _submit_input(self) -> None:
        input_widget = self.query_one("#agent-input", TextArea)
        text = input_widget.text.strip()
        if not text:
            return
        input_widget.clear()
        self.query_one("#slash-completions", OptionList).display = False
        self._history.append(text)
        self._history_index = -1

        if text.startswith("/"):
            self._handle_slash_command(text)
            return

        self._append_user_message(text)
        self._input_queue.put_nowait(text)

    def _handle_slash_command(self, command: str) -> None:
        chat = self.query_one("#chat-view", VerticalScroll)
        if command == "/quit":
            self._input_queue.put_nowait(QUIT_SENTINEL)
            self.exit()
        elif command == "/help":
            lines = "  ".join(f"{k} — {v}\n" for k, v in _SLASH_COMMANDS.items())
            chat.mount(Static(f"  {lines}", classes="user-message"))
            self._auto_scroll()
        elif command == "/session":
            sid = self._session_id or "not connected"
            chat.mount(Static(f"  session {sid}", classes="user-message"))
            self._auto_scroll()
        elif command == "/clear":
            chat.remove_children()
            self._current_stream = None
            self._current_thinking = None
            self._pending_tools.clear()
            self._agent_label_shown = False

    def _append_user_message(self, text: str) -> None:
        chat = self.query_one("#chat-view", VerticalScroll)
        chat.mount(Static("You", classes="user-label"))
        chat.mount(Static(f"  {text}", classes="user-message"))
        self._auto_scroll()

    async def on_key(self, event) -> None:
        input_widget = self.query_one("#agent-input", TextArea)

        if self._elicitation_future is not None and not self._elicitation_future.done():
            if event.key in ("y", "Y"):
                if self._elicitation_url:
                    webbrowser.open(self._elicitation_url)
                self._elicitation_future.set_result("accept")
                self._elicitation_future = None
                self._reset_status_bar()
                event.prevent_default()
                return
            if event.key in ("n", "N"):
                self._elicitation_future.set_result("decline")
                self._elicitation_future = None
                self._reset_status_bar()
                event.prevent_default()
                return

        if event.key == "enter" and input_widget.has_focus:
            event.prevent_default()
            self._submit_input()
            return

        if event.key == "escape" and input_widget.has_focus:
            input_widget.clear()
            self.query_one("#slash-completions", OptionList).display = False
            event.prevent_default()
            return

        if event.key == "up" and input_widget.has_focus and input_widget.text == "":
            if self._history:
                if self._history_index == -1:
                    self._history_index = len(self._history) - 1
                elif self._history_index > 0:
                    self._history_index -= 1
                input_widget.load_text(self._history[self._history_index])
            event.prevent_default()
            return

        if event.key == "down" and input_widget.has_focus and input_widget.text != "":
            if self._history_index != -1:
                if self._history_index < len(self._history) - 1:
                    self._history_index += 1
                    input_widget.load_text(self._history[self._history_index])
                else:
                    self._history_index = -1
                    input_widget.clear()
            event.prevent_default()
            return

    def action_quit_app(self) -> None:
        self._input_queue.put_nowait(QUIT_SENTINEL)
        self.exit()

    def on_session_info_received(self, message: SessionInfoReceived) -> None:
        self._session_id = message.session_id
        self._agent_name = message.agent_name
        header = self.query_one("#agent-header", Static)
        header.update(f"  {message.agent_name}  │  session {message.session_id[:12]}…")

    def on_reply_started(self, message: ReplyStarted) -> None:
        input_widget = self.query_one("#agent-input", TextArea)
        input_widget.disabled = True
        status = self.query_one("#status-bar", Static)
        status.update("  ● Thinking...")

    def on_reply_ended(self, message: ReplyEnded) -> None:
        self._finalize_current_thinking()
        self._current_stream = None
        self._agent_label_shown = False
        input_widget = self.query_one("#agent-input", TextArea)
        input_widget.disabled = False
        input_widget.focus()
        self._reset_status_bar()

    def on_token_received(self, message: TokenReceived) -> None:
        self._finalize_current_thinking()
        chat = self.query_one("#chat-view", VerticalScroll)
        self._ensure_agent_label(chat)
        if self._current_stream is None:
            self._current_stream = StreamBlock()
            chat.mount(self._current_stream)
        self._current_stream.append_token(message.text)
        self._auto_scroll()

    def on_reasoning_received(self, message: ReasoningReceived) -> None:
        chat = self.query_one("#chat-view", VerticalScroll)
        self._ensure_agent_label(chat)
        if self._current_thinking is None:
            self._current_thinking = ThinkingBlock()
            chat.mount(self._current_thinking)
        self._current_thinking.append_text(message.text)
        self._auto_scroll()

    def on_tool_started(self, message: ToolStarted) -> None:
        self._finalize_current_thinking()
        chat = self.query_one("#chat-view", VerticalScroll)
        self._ensure_agent_label(chat)
        block = ToolBlock(message.name, message.args)
        self._pending_tools.append(block)
        chat.mount(block)
        self._auto_scroll()

    def on_tool_completed(self, message: ToolCompleted) -> None:
        for i, block in enumerate(self._pending_tools):
            if block.tool_name == message.name:
                block.mark_done(message.status)
                self._pending_tools.pop(i)
                break

    def on_response_received(self, message: ResponseReceived) -> None:
        if self._current_stream is not None:
            self._current_stream.finalize(message.content)
            self._current_stream = None
        elif message.content:
            chat = self.query_one("#chat-view", VerticalScroll)
            self._ensure_agent_label(chat)
            chat.mount(Markdown(message.content))
        self._auto_scroll()

    def on_elicitation_requested(self, message: ElicitationRequested) -> None:
        chat = self.query_one("#chat-view", VerticalScroll)
        prompt = ElicitationPrompt(
            server_name=message.request.get("server_name", "unknown"),
            message=message.request.get("message", "Authorization required"),
        )
        chat.mount(prompt)
        self._auto_scroll()
        self._elicitation_future = message.future
        self._elicitation_url = message.request.get("url", "")
        status = self.query_one("#status-bar", Static)
        status.update("  [Y]es to authorize  │  [N]o to decline")

    def on_session_ended(self, message: SessionEnded) -> None:
        chat = self.query_one("#chat-view", VerticalScroll)
        chat.mount(
            Static(
                f"  Session ended: {message.reason} ({message.turns} turns)",
                classes="user-message",
            ),
        )
        self._auto_scroll()

    def _finalize_current_thinking(self) -> None:
        if self._current_thinking is not None:
            self._current_thinking.finalize()
            self._current_thinking = None

    def _ensure_agent_label(self, chat: VerticalScroll) -> None:
        if not self._agent_label_shown:
            self._agent_label_shown = True
            chat.mount(Static("Agent", classes="agent-label"))

    def _reset_status_bar(self) -> None:
        status = self.query_one("#status-bar", Static)
        status.update(_DEFAULT_STATUS)

    def _auto_scroll(self) -> None:
        chat = self.query_one("#chat-view", VerticalScroll)
        if chat.scroll_y >= chat.max_scroll_y - 2:
            chat.scroll_end(animate=False)
