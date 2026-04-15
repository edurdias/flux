from __future__ import annotations

import asyncio
import json

from fastapi import Body, FastAPI, Query
from sse_starlette.sse import EventSourceResponse

from flux.agents.types import SessionEndOutput
from flux.agents.ui import UI


class ApiUI(UI):
    def __init__(self, port: int | None = None):
        self.port = port or 8080
        self.app = FastAPI(title="Flux Agent API")
        self._setup_routes()
        self._event_queue: asyncio.Queue = asyncio.Queue()

    def _setup_routes(self):
        @self.app.post("/chat")
        async def chat(
            message: str = Body(None, embed=True),
            session: str = Query(None),
        ):
            return EventSourceResponse(self._stream_placeholder())

        @self.app.post("/elicitation/{elicitation_id}")
        async def elicitation_response(
            elicitation_id: str,
            response: dict = Body(...),
        ):
            return {"status": "ok"}

        @self.app.get("/session/{session_id}")
        async def get_session(session_id: str):
            return {"session_id": session_id, "status": "unknown"}

        @self.app.get("/health")
        async def health():
            return {"status": "ok"}

    async def _stream_placeholder(self):
        yield {"data": json.dumps({"type": "connected"})}

    async def display_response(self, content: str | None) -> None:
        if content is not None:
            await self._event_queue.put({"type": "response", "content": content})

    async def display_tool_start(self, name: str, args: dict) -> None:
        await self._event_queue.put({"type": "tool_start", "name": name, "args": args})

    async def display_tool_done(self, name: str, status: str) -> None:
        await self._event_queue.put({"type": "tool_done", "name": name, "status": status})

    async def display_token(self, text: str) -> None:
        await self._event_queue.put({"type": "token", "text": text})

    async def display_elicitation(self, request: dict) -> dict:
        await self._event_queue.put({"type": "elicitation", **request})
        return {}

    async def prompt_user(self) -> str:
        raise NotImplementedError("API mode does not prompt")

    async def display_session_info(self, session_id: str, agent_name: str) -> None:
        pass

    async def display_session_end(self, output: SessionEndOutput) -> None:
        await self._event_queue.put({"type": "session_end", **output.model_dump()})

    async def start(self):
        import uvicorn

        config = uvicorn.Config(self.app, host="0.0.0.0", port=self.port)
        server = uvicorn.Server(config)
        await server.serve()
