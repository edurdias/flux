from __future__ import annotations

import asyncio
import shutil
import sys
import webbrowser

from flux.agents.types import SessionEndOutput
from flux.agents.ui import UI

# ANSI escape sequences
_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_ITALIC = "\033[3m"
_BLUE = "\033[34m"
_CYAN = "\033[36m"
_GREEN = "\033[32m"
_RED = "\033[31m"
_YELLOW = "\033[33m"
_MAGENTA = "\033[35m"
_WHITE = "\033[97m"
_DIM_WHITE = "\033[37m"
_BG_DARK = "\033[48;5;236m"

_SPINNER_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")


def _supports_color() -> bool:
    if not hasattr(sys.stdout, "isatty") or not sys.stdout.isatty():
        return False
    return True


def _hr() -> str:
    cols = shutil.get_terminal_size((80, 24)).columns
    return f"{_DIM}{'─' * cols}{_RESET}"


class TerminalUI(UI):
    def __init__(self) -> None:
        self._color = _supports_color()
        self._spinner_task: asyncio.Task | None = None
        self._thinking_lines: int = 0

    def _c(self, code: str, text: str) -> str:
        if not self._color:
            return text
        return f"{code}{text}{_RESET}"

    async def _start_spinner(self, label: str = "Thinking") -> None:
        await self._stop_spinner()

        async def _spin() -> None:
            i = 0
            while True:
                frame = _SPINNER_FRAMES[i % len(_SPINNER_FRAMES)]
                sys.stdout.write(f"\r{_DIM}{frame} {label}...{_RESET}  ")
                sys.stdout.flush()
                i += 1
                await asyncio.sleep(0.1)

        self._spinner_task = asyncio.create_task(_spin())

    async def _stop_spinner(self) -> None:
        if self._spinner_task and not self._spinner_task.done():
            self._spinner_task.cancel()
            try:
                await self._spinner_task
            except asyncio.CancelledError:
                pass
            sys.stdout.write("\r\033[2K")
            sys.stdout.flush()
            self._spinner_task = None

    async def display_response(self, content: str | None) -> None:
        await self._stop_spinner()
        if content is not None:
            print(f"\n{content}\n")

    async def display_tool_start(self, tool_id: str, name: str, args: dict) -> None:
        await self._stop_spinner()
        args_str = ", ".join(f"{k}={v!r}" for k, v in args.items())
        tool_label = self._c(f"{_BOLD}{_CYAN}", name)
        print(f"\n  {self._c(_DIM, '◆')} {tool_label}{self._c(_DIM, f'({args_str})')}")
        await self._start_spinner("Running")

    async def display_tool_done(self, tool_id: str, name: str, status: str) -> None:
        await self._stop_spinner()
        if status == "success":
            icon = self._c(_GREEN, "✓")
        else:
            icon = self._c(_RED, "✗")
        print(f"  {icon} {self._c(_DIM, name)}")

    async def display_token(self, text: str) -> None:
        await self._stop_spinner()
        self._thinking_lines = 0
        sys.stdout.write(text)
        sys.stdout.flush()

    async def display_reasoning(self, text: str) -> None:
        await self._stop_spinner()
        if self._thinking_lines == 0:
            print(f"\n  {self._c(f'{_DIM}{_ITALIC}', '▸ Thinking...')}")
        lines = text.strip().split("\n")
        for line in lines:
            print(f"  {self._c(_DIM, '│')} {self._c(_DIM, line)}")
        self._thinking_lines += len(lines)

    async def display_elicitation(self, request: dict) -> dict:
        await self._stop_spinner()
        server_name = request.get("server_name", "unknown")
        message = request.get("message", "Authorization required")
        url = request.get("url", "")
        elicitation_id = request.get("elicitation_id", "")

        print(f"\n  {self._c(_YELLOW, '⚡')} {self._c(_BOLD, server_name)}: {message}")
        answer = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: input(f"  Open browser to authorize? {self._c(_DIM, '[Y/n]')} "),
        )

        if answer.strip().lower() in ("", "y", "yes"):
            webbrowser.open(url)
            print(f"  {self._c(_DIM, 'Opening browser... waiting for authorization.')}")
            return {
                "elicitation_response": {
                    "elicitation_id": elicitation_id,
                    "action": "accept",
                },
            }
        else:
            return {
                "elicitation_response": {
                    "elicitation_id": elicitation_id,
                    "action": "decline",
                },
            }

    async def prompt_user(self) -> str:
        self._thinking_lines = 0
        print()
        prompt = f"{_BOLD}{_BLUE}>{_RESET} " if self._color else "> "
        line = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: input(prompt),
        )
        return line

    async def begin_reply(self) -> None:
        await self._start_spinner()

    async def end_reply(self) -> None:
        await self._stop_spinner()

    async def display_session_info(self, session_id: str, agent_name: str) -> None:
        print()
        print(_hr())
        print(
            f"  {self._c(_BOLD, agent_name)}"
            f"  {self._c(_DIM, '│')}"
            f"  {self._c(_DIM, f'session {session_id[:12]}…')}",
        )
        print(_hr())
        print(
            f"  {self._c(_DIM, 'Type')} {self._c(f'{_DIM}{_BOLD}', '/help')}"
            f" {self._c(_DIM, 'for commands, Ctrl+D to exit.')}",
        )
        print()

    async def display_session_end(self, output: SessionEndOutput) -> None:
        await self._stop_spinner()
        print()
        print(_hr())
        print(
            f"  {self._c(_DIM, 'Session ended:')} {output.reason}"
            f" {self._c(_DIM, f'({output.turns} turns)')}",
        )
        print(_hr())
