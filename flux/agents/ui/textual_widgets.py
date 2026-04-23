"""Custom Textual widgets for the agent TUI."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Collapsible, Markdown, Static


class StreamBlock(Vertical):
    """Accumulates streamed tokens and can finalize into rendered Markdown."""

    DEFAULT_CSS = """
    StreamBlock {
        height: auto;
        padding: 0 1;
    }
    StreamBlock .stream-text {
        height: auto;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.buffer = ""
        self._text_widget = Static("", classes="stream-text")
        self._finalized = False

    def compose(self) -> ComposeResult:
        yield self._text_widget

    def append_token(self, text: str) -> None:
        if self._finalized:
            return
        self.buffer += text
        self._text_widget.update(self.buffer)

    def finalize(self, content: str | None = None) -> None:
        if self._finalized:
            return
        self._finalized = True
        final = content if content is not None else self.buffer
        if not final:
            return
        self._text_widget.remove()
        self.mount(Markdown(final))


class ToolBlock(Static):
    """Displays a tool call with status indicator."""

    DEFAULT_CSS = """
    ToolBlock {
        height: auto;
        padding: 0 1;
    }
    """

    def __init__(self, name: str, args: dict, **kwargs) -> None:
        super().__init__(**kwargs)
        self.tool_name = name
        self.tool_args = args
        self._status: str | None = None

    def render_text(self) -> str:
        args_str = ", ".join(f"{k}={v!r}" for k, v in self.tool_args.items())
        label = f"{self.tool_name}({args_str})"
        if self._status is None:
            return f"  \u25c6 {label}"
        if self._status == "success":
            return f"  \u2713 {label}"
        return f"  \u2717 {label}"

    def render(self) -> str:
        return self.render_text()

    def mark_done(self, status: str) -> None:
        self._status = status
        self.update(self.render_text())


class ThinkingBlock(Collapsible):
    """Collapsible reasoning/thinking block."""

    DEFAULT_CSS = """
    ThinkingBlock {
        height: auto;
        padding: 0 1;
    }
    ThinkingBlock Static {
        color: $text-muted;
    }
    """

    def __init__(self, **kwargs) -> None:
        self._content = Static("", classes="thinking-content")
        self._lines: list[str] = []
        super().__init__(self._content, title="Thinking...", collapsed=True, **kwargs)

    @property
    def line_count(self) -> int:
        return len(self._lines)

    def append_text(self, text: str) -> None:
        new_lines = text.split("\n")
        self._lines.extend(new_lines)
        self._content.update("\n".join(self._lines))

    def finalize(self) -> None:
        count = len(self._lines)
        self.title = f"Thinking ({count} line{'s' if count != 1 else ''})"


class ElicitationPrompt(Static):
    """Inline permission/authorization prompt."""

    DEFAULT_CSS = """
    ElicitationPrompt {
        height: auto;
        padding: 0 1;
        color: $warning;
    }
    """

    def __init__(self, server_name: str, message: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self.server_name = server_name
        self.elicitation_message = message

    def render_text(self) -> str:
        return (
            f"  \u26a1 {self.server_name}: {self.elicitation_message}\n"
            f"  Open browser to authorize? [Y]es / [N]o"
        )

    def render(self) -> str:
        return self.render_text()
