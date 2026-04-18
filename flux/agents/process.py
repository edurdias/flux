from __future__ import annotations

from flux.agents.events import AgentEvent
from flux.agents.flux_client import FluxClient
from flux.agents.session import AgentSession
from flux.agents.ui import UI
from flux.agents.ui.terminal import TerminalUI


VALID_MODES = ("terminal", "web", "api")


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
        self.ui: UI | None = TerminalUI() if mode == "terminal" else None

    async def run(self) -> None:
        if self.mode == "terminal":
            await self._run_terminal()
        else:
            await self._run_server()

    async def _run_terminal(self) -> None:
        assert self.ui is not None
        session = AgentSession(
            client=self.client,
            agent_name=self.agent_name,
            session_id=self.session_id,
            workflow_name=self.workflow_name,
        )

        if self.session_id is None:
            async for event in session.start():
                await self._dispatch(event, session)

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
                    print(f"Session: {self.session_id}")
                    continue
                if stripped == "/help":
                    print("Commands: /help, /session, /quit")
                    continue
                if not stripped:
                    continue

                async for event in session.send(user_input):
                    await self._dispatch(event, session)
        except KeyboardInterrupt:
            pass

        print(f"\nSession: {self.session_id}")

    async def _dispatch(self, event: AgentEvent, session: AgentSession) -> None:
        assert self.ui is not None
        if event.kind == "token":
            await self.ui.display_token(event.data["text"])
        elif event.kind == "tool_start":
            await self.ui.display_tool_start(event.data["name"], event.data["args"])
        elif event.kind == "tool_done":
            await self.ui.display_tool_done(event.data["name"], event.data["status"])
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
