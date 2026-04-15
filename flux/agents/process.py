from __future__ import annotations

from flux.agents.flux_client import FluxClient
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
    ):
        if mode not in VALID_MODES:
            raise ValueError(f"Invalid mode: '{mode}'. Must be one of: {VALID_MODES}")

        self.agent_name = agent_name
        self.server_url = server_url
        self.mode = mode
        self.session_id = session_id
        self.port = port
        self.client = FluxClient(server_url=server_url, token=token)
        self.ui: UI = self._create_ui(mode)

    def _create_ui(self, mode: str) -> UI:
        if mode == "terminal":
            return TerminalUI()
        elif mode == "web":
            from flux.agents.ui.web import WebUI

            return WebUI(port=self.port)
        elif mode == "api":
            from flux.agents.ui.api import ApiUI

            return ApiUI(port=self.port)
        raise ValueError(f"Unknown mode: {mode}")

    async def run(self) -> None:
        if self.mode == "terminal":
            await self._run_terminal()
        elif self.mode in ("web", "api"):
            await self._run_server()

    async def _run_terminal(self) -> None:
        if not self.session_id:
            self.session_id = await self._start_new_session()

        await self.ui.display_session_info(self.session_id, self.agent_name)

        try:
            while True:
                try:
                    user_input = await self.ui.prompt_user()
                except EOFError:
                    break

                if user_input.strip() == "/quit":
                    break
                elif user_input.strip() == "/session":
                    print(f"Session: {self.session_id}")
                    continue
                elif user_input.strip() == "/help":
                    print("Commands: /help, /session, /quit")
                    continue
                elif not user_input.strip():
                    continue

                await self._send_message(user_input)
        except KeyboardInterrupt:
            pass

        print(f"\nSession: {self.session_id}")

    async def _start_new_session(self) -> str:
        execution_id = None
        async for eid, event in self.client.start_agent(self.agent_name):
            if eid is not None:
                execution_id = eid
            await self._handle_event(event)
        if execution_id is None:
            raise RuntimeError("Failed to get execution ID from server")
        return execution_id

    async def _send_message(self, message: str) -> None:
        async for event in self.client.resume(
            self.session_id, message, namespace="agents"
        ):
            await self._handle_event(event)

    async def _handle_event(self, event: dict) -> None:
        event_type = event.get("type", "")

        if event_type == "task.progress":
            value = event.get("value", {})
            progress_type = value.get("type", "")

            if progress_type == "token":
                await self.ui.display_token(value.get("text", ""))
            elif progress_type == "tool_start":
                await self.ui.display_tool_start(
                    value.get("name", ""), value.get("args", {})
                )
            elif progress_type == "tool_done":
                await self.ui.display_tool_done(
                    value.get("name", ""), value.get("status", "")
                )

        elif "paused" in event_type:
            output = event.get("output", {})
            pause_type = output.get("type", "")

            if pause_type == "chat_response":
                await self.ui.display_response(output.get("content"))
            elif pause_type == "elicitation":
                await self.ui.display_elicitation(output)

    async def _run_server(self) -> None:
        raise NotImplementedError("Web/API mode not yet implemented")
