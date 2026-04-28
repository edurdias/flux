from __future__ import annotations

import json
from collections.abc import AsyncIterator

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query
from sse_starlette.sse import EventSourceResponse

from flux.agents.events import AgentEvent
from flux.agents.flux_client import FluxClient
from flux.agents.session import AgentSession


def _event_to_sse_payload(event: AgentEvent) -> dict:
    """Map AgentEvent to the SSE shape the web/client expects."""
    if event.kind == "chat_response":
        return {"type": "response", **event.data}
    if event.kind == "session_id":
        return {"type": "session_id", **event.data}
    return {"type": event.kind, **event.data}


class ApiUI:
    """HTTP/SSE agent API.

    Every request requires a Bearer token; that token is passed through to
    the Flux server on a per-request basis. The operator_token (set at
    process-start time) is only used by WebUI, which overrides the auth
    dependency.
    """

    def __init__(
        self,
        server_url: str,
        agent_name: str,
        operator_token: str | None = None,
        port: int = 8080,
        workflow_name: str = "agent_chat",
    ) -> None:
        self.server_url = server_url
        self.agent_name = agent_name
        self.operator_token = operator_token
        self.port = port
        self.workflow_name = workflow_name
        self.app = FastAPI(title="Flux Agent API")
        self._setup_routes()

    def _extract_token(self, authorization: str | None) -> str:
        """API auth: require a Bearer token on every request."""
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(
                status_code=401,
                detail="Missing or invalid Authorization header",
            )
        token = authorization.split(" ", 1)[1].strip()
        if not token:
            raise HTTPException(status_code=401, detail="Empty Bearer token")
        return token

    def _make_client(self, token: str | None) -> FluxClient:
        return FluxClient(server_url=self.server_url, token=token)

    def _get_token_dependency(self):
        """Overridable hook for subclasses (WebUI) to change auth behavior."""

        def _dep(authorization: str | None = Header(default=None)) -> str:
            return self._extract_token(authorization)

        return _dep

    def _setup_routes(self) -> None:
        @self.app.get("/health")
        async def health() -> dict:
            return {"status": "ok"}

        token_dep = self._get_token_dependency()

        @self.app.post("/chat")
        async def chat(
            body: dict = Body(default_factory=dict),
            session: str | None = Query(default=None),
            token: str = Depends(token_dep),
        ):
            message = body.get("message", "")
            client = self._make_client(token)
            agent_session = AgentSession(
                client=client,
                agent_name=self.agent_name,
                session_id=session,
                workflow_name=self.workflow_name,
            )

            async def event_stream() -> AsyncIterator[dict]:
                try:
                    if session is None:
                        async for event in agent_session.start():
                            yield {"data": json.dumps(_event_to_sse_payload(event))}
                        if message:
                            async for event in agent_session.send(message):
                                yield {"data": json.dumps(_event_to_sse_payload(event))}
                    else:
                        async for event in agent_session.send(message):
                            yield {"data": json.dumps(_event_to_sse_payload(event))}
                except Exception as exc:  # noqa: BLE001
                    yield {"data": json.dumps({"type": "error", "message": str(exc)})}

            return EventSourceResponse(event_stream())

        @self.app.post("/elicitation/{elicitation_id}")
        async def elicitation(
            elicitation_id: str,
            body: dict = Body(...),
            session: str = Query(...),
            token: str = Depends(token_dep),
        ):
            client = self._make_client(token)
            agent_session = AgentSession(
                client=client,
                agent_name=self.agent_name,
                session_id=session,
                workflow_name=self.workflow_name,
            )
            action = body.get("action", "decline")
            allowed_actions = ("accept", "decline", "cancel")
            if action not in allowed_actions:
                supported = ", ".join(allowed_actions)
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid elicitation action: '{action}'. Must be one of: {supported}",
                )
            payload = {
                "elicitation_response": {
                    "elicitation_id": body.get("elicitation_id", elicitation_id),
                    "action": action,
                },
            }

            async def event_stream() -> AsyncIterator[dict]:
                try:
                    async for event in agent_session.respond_to_elicitation(payload):
                        yield {"data": json.dumps(_event_to_sse_payload(event))}
                except Exception as exc:  # noqa: BLE001
                    yield {"data": json.dumps({"type": "error", "message": str(exc)})}

            return EventSourceResponse(event_stream())

        @self.app.get("/session/{session_id}")
        async def get_session(
            session_id: str,
            token: str = Depends(token_dep),
        ):
            client = self._make_client(token)
            return await client.get_execution(session_id)

    async def serve(self) -> None:
        import uvicorn

        config = uvicorn.Config(self.app, host="0.0.0.0", port=self.port, log_level="info")
        server = uvicorn.Server(config)
        await server.serve()
