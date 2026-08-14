from __future__ import annotations

import json
from collections.abc import AsyncIterator

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query
from sse_starlette.sse import EventSourceResponse

from flux.agents.console.app import mount_console_routes
from flux.agents.events import AgentEvent
from flux.agents.flux_client import FluxClient
from flux.agents.session import AgentSession

# console runs multi-session; legacy single-agent routes need a concrete name.
_NO_AGENT_DETAIL = "console runs multi-session — use /console/*"


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
        agent_name: str | None,
        operator_token: str | None = None,
        port: int = 8080,
        workflow_name: str = "agent_chat",
        host: str = "127.0.0.1",
        allowed_origins: tuple[str, ...] = (),
        session_id: str | None = None,
        allow_remote: bool = False,
    ) -> None:
        self.server_url = server_url
        self.agent_name = agent_name
        self.operator_token = operator_token
        self.session_id = session_id
        self.host = host
        self.port = port
        self.workflow_name = workflow_name
        self.allowed_origins = allowed_origins
        self.allow_remote = allow_remote
        self.app = FastAPI(title="Flux Agent API")
        self._setup_routes()
        mount_console_routes(
            self.app,
            server_url=self.server_url,
            agent_name=self.agent_name,
            token_dependency=self._get_token_dependency(),
            csrf_dependency=self._csrf_dependency(),
            session_id=self.session_id,
        )

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

    def _origin_allowlist(self) -> set[str]:
        """Origins the console's own frontend can legitimately present.

        Built from the bind host/port (not hardcoded) so a non-default
        --host still gets a working allowlist; 127.0.0.1/localhost are
        always included since browsers treat a loopback server as
        reachable under either name interchangeably. Wildcard binds
        (0.0.0.0 / ::) never appear in a browser's Origin header, so they
        contribute nothing — an externally exposed console must name the
        origins operators will actually use via ``allowed_origins``
        (`flux agent start --allow-origin`).
        """
        hosts = {self.host, "127.0.0.1", "localhost"} - {"0.0.0.0", "::", "[::]"}
        origins = {f"http://{host}:{self.port}" for host in hosts}
        origins.update(origin.rstrip("/") for origin in self.allowed_origins)
        return origins

    def _csrf_dependency(self):
        """Origin/CSRF defense for state-changing routes.

        Requires the custom `X-Flux-Console` header (forces a CORS
        preflight a third-party site cannot silently pass) and, only when
        the browser actually sent an Origin header, a match against this
        console's own origin. Applied to every POST/PUT route -- including
        the pre-existing /chat, /approval, /elicitation, which were
        drive-by-POSTable from any website before this hardening.
        """
        allowlist = self._origin_allowlist()

        def _dep(
            x_flux_console: str | None = Header(default=None, alias="X-Flux-Console"),
            origin: str | None = Header(default=None),
        ) -> None:
            if x_flux_console != "1":
                raise HTTPException(
                    status_code=403,
                    detail="Missing required 'X-Flux-Console' header",
                )
            if origin is not None and origin not in allowlist:
                raise HTTPException(status_code=403, detail="Origin not allowed")

        return _dep

    def _setup_routes(self) -> None:
        @self.app.get("/health")
        async def health() -> dict:
            return {"status": "ok"}

        token_dep = self._get_token_dependency()
        csrf_dep = self._csrf_dependency()

        @self.app.post("/chat", dependencies=[Depends(csrf_dep)])
        async def chat(
            body: dict = Body(default_factory=dict),
            session: str | None = Query(default=None),
            token: str = Depends(token_dep),
        ):
            agent_name = self.agent_name
            if agent_name is None:
                raise HTTPException(status_code=404, detail=_NO_AGENT_DETAIL)
            message = body.get("message", "")
            client = self._make_client(token)
            agent_session = AgentSession(
                client=client,
                agent_name=agent_name,
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

        @self.app.post("/elicitation/{elicitation_id}", dependencies=[Depends(csrf_dep)])
        async def elicitation(
            elicitation_id: str,
            body: dict = Body(...),
            session: str = Query(...),
            token: str = Depends(token_dep),
        ):
            client = self._make_client(token)
            # This route always resumes an already-started session, which
            # never reads agent_name (only .start() does) -- the "or ''" is
            # just satisfying AgentSession's str-typed parameter.
            agent_session = AgentSession(
                client=client,
                agent_name=self.agent_name or "",
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

        @self.app.post("/approval/{task_call_id:path}", dependencies=[Depends(csrf_dep)])
        async def approval(
            task_call_id: str,
            body: dict = Body(...),
            session: str = Query(...),
            token: str = Depends(token_dep),
        ):
            approved = body.get("approved")
            if not isinstance(approved, bool):
                raise HTTPException(
                    status_code=400,
                    detail="Approval decision requires a boolean 'approved' field.",
                )
            reason = body.get("reason")
            # The gated task runs inside the agent execution; fall back to the
            # session id when the client does not echo the event's execution_id.
            execution_id = body.get("execution_id") or session
            client = self._make_client(token)
            # Same as /elicitation: only .start() reads agent_name, and this
            # route always resumes an already-running session.
            agent_session = AgentSession(
                client=client,
                agent_name=self.agent_name or "",
                session_id=session,
                workflow_name=self.workflow_name,
            )

            async def event_stream() -> AsyncIterator[dict]:
                try:
                    await client.decide_approval(
                        execution_id,
                        task_call_id,
                        approved=approved,
                        reason=reason,
                    )
                    # The decide route resumes the workflow without an SSE
                    # response of its own; re-attach to surface the events
                    # produced after the decision.
                    async for event in agent_session.reattach():
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

    def _startup_banner(self) -> list[str]:
        """Lines printed before the server binds. Nothing was printed
        before, so operators had to guess the URL."""
        return [f"Agent API: http://{self.host}:{self.port}"]

    async def serve(self) -> None:
        import uvicorn

        for line in self._startup_banner():
            print(line)

        config = uvicorn.Config(self.app, host=self.host, port=self.port, log_level="info")
        server = uvicorn.Server(config)
        await server.serve()
