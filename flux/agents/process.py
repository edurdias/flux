from __future__ import annotations

import logging

from flux.agents.events import AgentEvent
from flux.agents.flux_client import FluxClient
from flux.agents.session import AgentSession
from flux.agents.ui import UI
from flux.agents.ui.terminal import TerminalUI

VALID_MODES = ("terminal", "web", "api")


def _make_terminal_ui() -> UI | None:
    """Decide between the plain single-agent REPL and the multi-session console.

    Returns ``None`` to mean "use the console" -- the console is a full
    Textual app that needs a real terminal, so an explicit ``--plain``
    (``FLUX_PLAIN_TERMINAL``) or stdout not being a tty both force the old
    plain REPL instead.
    """
    import os
    import sys

    if os.environ.get("FLUX_PLAIN_TERMINAL") or not sys.stdout.isatty():
        return TerminalUI()
    return None


class AgentProcess:
    def __init__(
        self,
        agent_name: str | None,
        server_url: str,
        mode: str = "terminal",
        session_id: str | None = None,
        token: str | None = None,
        port: int | None = None,
        workflow_name: str = "agent_chat",
        host: str | None = None,
    ):
        if mode not in VALID_MODES:
            raise ValueError(f"Invalid mode: '{mode}'. Must be one of: {VALID_MODES}")

        self.agent_name = agent_name
        self.server_url = server_url
        self.mode = mode
        self.session_id = session_id
        self.token = token
        self.port = port
        self.host = host
        self.workflow_name = workflow_name
        self.client = FluxClient(server_url=server_url, token=token)
        self.ui: UI | None = _make_terminal_ui() if mode == "terminal" else None

        # `agent_name=None` is only meaningful for the modes that route
        # through the multi-session console (terminal without --plain, web,
        # api) -- the plain REPL and the legacy /chat route both need one
        # concrete agent to start or resume a session against.
        if agent_name is None and self.ui is not None:
            raise ValueError(
                "agent_name is required for the plain terminal UI; "
                "omit --plain (or provide an agent name) to use the console",
            )

    async def run(self) -> None:
        await self.client.ensure_workflow_registered(
            workflow_name=self.workflow_name,
        )
        if self.mode == "terminal":
            await self._run_terminal()
        else:
            await self._run_server()

    async def _run_terminal(self) -> None:
        if self.ui is not None:
            await self._run_plain_terminal()
        else:
            await self._run_console()

    async def _run_plain_terminal(self) -> None:
        assert self.ui is not None
        # Guaranteed by __init__ (agent_name=None raises unless self.ui is
        # None, i.e. console mode) -- narrows the type for AgentSession and
        # display_session_info below, both of which require a concrete name.
        assert self.agent_name is not None
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

    async def _run_console(self) -> None:
        """Boot the multi-session console (Task 8's ``ConsoleApp``).

        ``agent_name`` becomes the rail's initial agent filter (``None`` for
        the rail-first, every-agent view); ``session_id`` -- when set, e.g.
        by ``agent session resume`` -- opens straight into that session.
        Unlike the plain REPL, the console owns its own server calls through
        ``ConsoleService``/``EventHub``, so there is no ``AgentSession`` or
        ``self._dispatch`` in this path.
        """
        from flux.agents.console.hub import EventHub
        from flux.agents.console.service import ConsoleService
        from flux.agents.ui.textual_app import ConsoleApp

        # Suppress Textual's internal logging so it doesn't bleed to stderr.
        logging.getLogger("textual").setLevel(logging.CRITICAL)
        logging.getLogger("textual.css").setLevel(logging.CRITICAL)

        service = ConsoleService(self.server_url, self.token)
        hub = EventHub(service)
        app = ConsoleApp(
            service,
            hub,
            initial_agent=self.agent_name,
            initial_session=self.session_id,
        )
        try:
            await app.run_async()
        finally:
            await service.aclose()

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
        elif event.kind == "approval_required":
            await self._handle_approval_request(event.data, session)
        elif event.kind == "session_id":
            pass

    async def _handle_approval_request(self, data: dict, session: AgentSession) -> None:
        """Resolve an approval_required event.

        Asks the UI for a decision and POSTs it. Only POSTs the decision when
        the UI actually returns one (api/web UIs return ``{'defer': True}`` and
        rely on the consumer to call the approve/reject HTTP routes).

        After a decision is posted the execution resumes server-side, but the
        SSE stream that delivered this event has already ended at the PAUSED
        frame. The session therefore re-attaches to the execution's event
        stream and dispatches the events produced after the decision —
        otherwise the UI would sit idle while the workflow continues.
        """
        assert self.ui is not None
        execution_id = data.get("execution_id", "")
        task_call_id = data.get("task_call_id", "")

        decision = await self.ui.display_approval_request(data)
        if decision.get("defer"):
            return
        await self.client.decide_approval(
            execution_id,
            task_call_id,
            approved=bool(decision.get("approved")),
            reason=decision.get("reason"),
            always=bool(decision.get("always")),
        )
        await self._stream_after_decision(session)

    async def _stream_after_decision(self, session: AgentSession) -> None:
        """Re-attach to the resumed execution and dispatch its events."""
        async for resume_event in session.reattach():
            await self._dispatch(resume_event, session)

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
            host=self.host or "127.0.0.1",
        )
        await server.serve()
