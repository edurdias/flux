"""Custom Textual widgets for the agent TUI."""

from __future__ import annotations

import time

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Collapsible, Markdown, Static


class StreamBlock(Vertical):
    """Accumulates streamed tokens and can finalize into rendered Markdown."""

    DEFAULT_CSS = """
    StreamBlock {
        height: auto;
        padding: 0 0 0 1;
        background: transparent;
    }
    StreamBlock .stream-text {
        height: auto;
    }
    StreamBlock Markdown {
        background: transparent;
        margin: 0;
        padding: 0;
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

    async def finalize(self, content: str | None = None) -> None:
        if self._finalized:
            return
        self._finalized = True
        final = content if content is not None else self.buffer
        if not final:
            return
        await self.remove_children()
        await self.mount(Markdown(final))


class ToolBlock(Static):
    """Compact single-line tool call with status icon and elapsed time."""

    DEFAULT_CSS = """
    ToolBlock {
        height: auto;
        padding: 0 0 0 1;
        color: $text-muted;
        background: transparent;
    }
    """

    def __init__(self, name: str, args: dict, **kwargs) -> None:
        super().__init__(**kwargs)
        self.tool_name = name
        self.tool_args = args
        self._status: str | None = None
        self._start_time = time.monotonic()
        self._elapsed: float | None = None

    def _format_args(self) -> str:
        if not self.tool_args:
            return ""
        parts = []
        for k, v in self.tool_args.items():
            s = repr(v)
            if len(s) > 40:
                s = s[:37] + "..."
            parts.append(f"{k}={s}")
        return ", ".join(parts)

    def render_text(self) -> str:
        args_str = self._format_args()
        label = f"{self.tool_name}({args_str})" if args_str else self.tool_name

        if self._status is None:
            return f"  \u25cb {label}"
        elapsed = f" {self._elapsed:.1f}s" if self._elapsed is not None else ""
        if self._status == "success":
            return f"  \u2713 {label}{elapsed}"
        return f"  \u2717 {label}{elapsed}"

    def render(self) -> str:
        return self.render_text()

    def mark_done(self, status: str) -> None:
        self._status = status
        self._elapsed = time.monotonic() - self._start_time
        self.update(self.render_text())


class ThinkingBlock(Collapsible):
    """Collapsible reasoning/thinking block with animated spinner while receiving."""

    DEFAULT_CSS = """
    ThinkingBlock {
        height: auto;
        padding: 0 0 0 1;
        background: transparent;
        border: none;
    }
    ThinkingBlock Contents {
        background: transparent;
    }
    ThinkingBlock CollapsibleTitle {
        background: transparent;
        color: $text-muted;
        padding: 0;
    }
    ThinkingBlock Static {
        color: $text-muted;
    }
    """

    def __init__(self, **kwargs) -> None:
        self._content = Static("", classes="thinking-content")
        self._buffer = ""
        self._start_time = time.monotonic()
        self._frame_index = 0
        self._anim_timer = None
        self._finalized = False
        super().__init__(self._content, title="Thinking", collapsed=True, **kwargs)

    def on_mount(self) -> None:
        self._anim_timer = self.set_interval(0.1, self._tick)

    def _tick(self) -> None:
        if self._finalized:
            return
        frame = _SPINNER_FRAMES[self._frame_index % len(_SPINNER_FRAMES)]
        self._frame_index += 1
        self.title = f"{frame} Thinking"

    @property
    def line_count(self) -> int:
        if not self._buffer:
            return 0
        return self._buffer.rstrip("\n").count("\n") + 1

    def append_text(self, text: str) -> None:
        self._buffer += text
        self._content.update(self._buffer)

    def finalize(self) -> None:
        self._finalized = True
        if self._anim_timer is not None:
            self._anim_timer.stop()
            self._anim_timer = None
        count = self.line_count
        elapsed = time.monotonic() - self._start_time
        self.title = f"Thinking ({count} line{'s' if count != 1 else ''}, {elapsed:.1f}s)"


_SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]


class SpinnerBlock(Static):
    """Inline animated spinner shown in the chat area during processing."""

    DEFAULT_CSS = """
    SpinnerBlock {
        height: 1;
        padding: 0 0 0 1;
        color: $text-muted;
        background: transparent;
    }
    """

    def __init__(self, label: str = "Thinking", **kwargs) -> None:
        super().__init__(**kwargs)
        self.label = label
        self._frame_index = 0
        self._timer = None

    def on_mount(self) -> None:
        self._timer = self.set_interval(0.1, self._tick)

    def _tick(self) -> None:
        frame = _SPINNER_FRAMES[self._frame_index % len(_SPINNER_FRAMES)]
        self._frame_index += 1
        self.update(f"  {frame} {self.label}")

    def set_label(self, label: str) -> None:
        self.label = label

    def stop(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None


class ElicitationPrompt(Static):
    """Inline permission/authorization prompt."""

    DEFAULT_CSS = """
    ElicitationPrompt {
        height: auto;
        padding: 0 1;
        color: $warning;
        background: transparent;
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
