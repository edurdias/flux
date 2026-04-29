from __future__ import annotations

import logging
import os

from flux.agents.events import AgentEvent
from flux.agents.flux_client import FluxClient
from flux.agents.session import AgentSession
from flux.agents.ui import UI
from flux.agents.ui.terminal import TerminalUI

logger = logging.getLogger("flux.agents")


VALID_MODES = ("terminal", "web", "api")


def _make_terminal_ui():
    import os
    import sys

    if os.environ.get("FLUX_PLAIN_TERMINAL") or not sys.stdout.isatty():
        return TerminalUI()
    try:
        from flux.agents.ui.textual_ui import TextualUI

        return TextualUI()
    except Exception:
        logger.debug(
            "Failed to initialize TextualUI, falling back to plain terminal",
            exc_info=True,
        )
        return TerminalUI()


class AgentProcess:
    def __init__(
        self,
        agent_name: str,
        server_url: str,
        mode: str = "terminal",
        session_id: str | None = None,
        token: str | None = None,
        port: int | None = None,
        workflow_name: str = "agent_chat",
    ):
        if mode not in VALID_MODES:
            raise ValueError(f"Invalid mode: '{mode}'. Must be one of: {VALID_MODES}")

        self.agent_name = agent_name
        self.server_url = server_url
        self.mode = mode
        self.session_id = session_id
        self.token = token
        self.port = port
        self.workflow_name = workflow_name
        self.client = FluxClient(server_url=server_url, token=token)
        self.ui: UI | None = _make_terminal_ui() if mode == "terminal" else None

    async def run(self) -> None:
        await self.client.ensure_workflow_registered(
            workflow_name=self.workflow_name,
        )
        if self.mode == "terminal":
            await self._run_terminal()
        else:
            await self._run_server()

    async def _run_terminal(self) -> None:
        assert self.ui is not None

        from flux.agents.ui.textual_ui import TextualUI

        if isinstance(self.ui, TextualUI):
            await self._run_textual_terminal()
        else:
            await self._run_plain_terminal()

    async def _run_plain_terminal(self) -> None:
        assert self.ui is not None
        session = AgentSession(
            client=self.client,
            agent_name=self.agent_name,
            session_id=self.session_id,
            workflow_name=self.workflow_name,
        )

        if self.session_id is None:
            await self.ui.begin_reply()
            async for event in session.start():
                await self._dispatch(event, session)
            await self.ui.end_reply()

        self.session_id = session.session_id
        assert self.session_id is not None

        await self.ui.display_session_info(self.session_id, self.agent_name)

        try:
            while True:
                try:
                    user_input = await self.ui.prompt_user()
                except EOFError:
                    break

                stripped = user_input.strip()
                if stripped == "/quit":
                    break
                if stripped == "/session":
                    print(f"\033[2m  session {self.session_id}\033[0m")
                    continue
                if stripped == "/help":
                    print(
                        "\033[2m  /help     — show this message\n"
                        "  /session  — show session id\n"
                        "  /quit     — end session\033[0m",
                    )
                    continue
                if not stripped:
                    continue

                await self.ui.begin_reply()
                async for event in session.send(user_input):
                    await self._dispatch(event, session)
                await self.ui.end_reply()
        except KeyboardInterrupt:
            pass

        print(f"\n\033[2m  session {self.session_id}\033[0m")

    async def _run_textual_terminal(self) -> None:
        import asyncio

        from flux.agents.ui.textual_ui import TextualUI

        assert isinstance(self.ui, TextualUI)
        ui: TextualUI = self.ui

        # Suppress Textual's internal logging so it doesn't bleed to stderr.
        logging.getLogger("textual").setLevel(logging.CRITICAL)
        logging.getLogger("textual.css").setLevel(logging.CRITICAL)

        async def session_loop() -> None:
            try:
                session = AgentSession(
                    client=self.client,
                    agent_name=self.agent_name,
                    session_id=self.session_id,
                    workflow_name=self.workflow_name,
                )

                if self.session_id is None:
                    await ui.begin_reply()
                    async for event in session.start():
                        await self._dispatch(event, session)
                    await ui.end_reply()

                self.session_id = session.session_id
                assert self.session_id is not None

                await ui.display_session_info(self.session_id, self.agent_name)

                try:
                    while True:
                        try:
                            user_input = await ui.prompt_user()
                        except EOFError:
                            break

                        if user_input == "\x04":
                            break
                        if not user_input.strip():
                            continue

                        await ui.begin_reply()
                        async for event in session.send(user_input):
                            await self._dispatch(event, session)
                        await ui.end_reply()
                except KeyboardInterrupt:
                    pass

            # Redirect stderr before exit() so Textual's teardown messages
            # ("Unmount()", "focus was removed") are suppressed.
            finally:
                self._saved_stderr_fd = os.dup(2)
                _devnull = os.open(os.devnull, os.O_WRONLY)
                os.dup2(_devnull, 2)
                os.close(_devnull)

            ui.app.exit()

        try:
            async with asyncio.TaskGroup() as tg:
                tg.create_task(ui.app.run_async())
                tg.create_task(session_loop())
        finally:
            # Restore stderr after Textual's teardown is complete, even if
            # the TaskGroup propagated an exception.
            saved = getattr(self, "_saved_stderr_fd", None)
            if saved is not None:
                os.dup2(saved, 2)
                os.close(saved)
                if hasattr(self, "_saved_stderr_fd"):
                    delattr(self, "_saved_stderr_fd")

    async def _dispatch(self, event: AgentEvent, session: AgentSession) -> None:
        assert self.ui is not None
        if event.kind == "token":
            await self.ui.display_token(event.data["text"])
        elif event.kind == "tool_start":
            await self.ui.display_tool_start(
                event.data.get("id", ""),
                event.data["name"],
                event.data["args"],
            )
        elif event.kind == "tool_done":
            await self.ui.display_tool_done(
                event.data.get("id", ""),
                event.data["name"],
                event.data["status"],
            )
        elif event.kind == "reasoning":
            await self.ui.display_reasoning(event.data["text"])
        elif event.kind == "chat_response":
            await self.ui.display_response(event.data["content"])
        elif event.kind == "session_end":
            from flux.agents.types import SessionEndOutput

            await self.ui.display_session_end(SessionEndOutput(**event.data))
        elif event.kind == "elicitation":
            response = await self.ui.display_elicitation(event.data)
            if response:
                async for resume_event in session.respond_to_elicitation(response):
                    await self._dispatch(resume_event, session)
        elif event.kind == "session_id":
            pass

    async def _run_server(self) -> None:
        from flux.agents.ui.api import ApiUI
        from flux.agents.ui.web import WebUI

        server_cls = WebUI if self.mode == "web" else ApiUI
        server = server_cls(
            server_url=self.server_url,
            agent_name=self.agent_name,
            operator_token=self.token,
            port=self.port or 8080,
            workflow_name=self.workflow_name,
        )
        await server.serve()
