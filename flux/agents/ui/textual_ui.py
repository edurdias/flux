"""TextualUI — bridges the UI ABC to the Textual AgentApp."""

from __future__ import annotations

import asyncio

from flux.agents.types import SessionEndOutput
from flux.agents.ui import UI
from flux.agents.ui.textual_app import AgentApp
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


class TextualUI(UI):
    """UI implementation that delegates to a full-screen Textual app."""

    def __init__(self) -> None:
        self._input_queue: asyncio.Queue[str] = asyncio.Queue()
        self.app = AgentApp(input_queue=self._input_queue)

    async def display_response(self, content: str | None) -> None:
        self.app.post_message(ResponseReceived(content))

    async def display_tool_start(self, name: str, args: dict) -> None:
        self.app.post_message(ToolStarted(name, args))

    async def display_tool_done(self, name: str, status: str) -> None:
        self.app.post_message(ToolCompleted(name, status))

    async def display_token(self, text: str) -> None:
        self.app.post_message(TokenReceived(text))

    async def display_reasoning(self, text: str) -> None:
        self.app.post_message(ReasoningReceived(text))

    async def display_elicitation(self, request: dict) -> dict:
        future: asyncio.Future[str] = asyncio.get_event_loop().create_future()
        self.app.post_message(ElicitationRequested(request, future))
        action = await future
        elicitation_id = request.get("elicitation_id", "")
        return {
            "elicitation_response": {
                "elicitation_id": elicitation_id,
                "action": action,
            },
        }

    async def prompt_user(self) -> str:
        return await self._input_queue.get()

    async def begin_reply(self) -> None:
        self.app.post_message(ReplyStarted())

    async def end_reply(self) -> None:
        self.app.post_message(ReplyEnded())

    async def display_session_info(self, session_id: str, agent_name: str) -> None:
        self.app.post_message(SessionInfoReceived(session_id, agent_name))

    async def display_session_end(self, output: SessionEndOutput) -> None:
        self.app.post_message(SessionEnded(output.reason, output.turns))
