"""The /console/* JSON+SSE endpoints shared by ApiUI (per-request Bearer)
and WebUI (fixed operator token).

``mount_console_routes`` adds routes onto an already-built FastAPI app
(``ApiUI.app``) rather than owning app construction itself -- security
(the Bearer-token dependency and the Origin/CSRF dependency) stays owned by
``flux/agents/ui/api.py``, which is also responsible for applying the CSRF
dependency to the pre-existing ``/chat``, ``/approval``, ``/elicitation``
routes it defines. This module only knows how to wire ``ConsoleService`` and
``EventHub`` to HTTP.

One ``EventHub`` (and its wrapped service) is built once per app (not once
per request) so the hub's title cache and subscriber fan-out survive across
requests -- both are meaningless if rebuilt every call. In ``api`` mode the
Bearer token varies per request, though, and that token *is* the
authorization boundary -- a naive shared, mutable ``service.token`` re-pointed
per request is a real cross-request leak, not a theoretical one: FastAPI runs
a sync dependency via a threadpool, which always yields to the event loop
between "set the token" and "the endpoint body reads it," so two overlapping
requests can interleave and read each other's credentials; a ``/send``
turn's background reconciliation (``EventHub.run_turn``'s ``finally``) makes
it worse, since it can execute long after its own request returned, using
whatever token the most recently *unrelated* request happened to install.
``_ScopedConsoleService`` below fixes this with a ``contextvars.ContextVar``
instead of a mutable attribute: it is set by an ``async def`` dependency
(never a sync one -- sync dependencies run in a threadpool copy of the
context, so a `.set()` there would never be visible back in the endpoint;
verified empirically before relying on it) so the value lives in the same
context the endpoint body runs in, and ``asyncio.create_task`` copies the
current context at creation time, so a turn's background task keeps the
token that was active when *it* was created for its whole lifetime,
regardless of what any later request does to the same shared service object.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
from collections.abc import Awaitable, Callable
from typing import TypeVar

import httpx
from fastapi import Body, Depends, FastAPI, HTTPException, Response
from sse_starlette.sse import EventSourceResponse

from flux.agents.console.hub import KIND_LOG_DELTA, EventHub
from flux.agents.console.service import ConsoleService
from flux.agents.flux_client import FluxClient

_T = TypeVar("_T")

# Console sessions always live in this namespace (ConsoleService.spawn/send
# hardcode it too) -- there is no per-session namespace to look up.
_NAMESPACE = "agents"

# The current request's (or, for a detached /send turn, the request that
# spawned it) Bearer token -- see the module docstring for why this has to
# be a contextvar rather than a mutable attribute on a shared service.
_request_token: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "console_request_token",
    default=None,
)


class _ScopedConsoleService(ConsoleService):
    """A ``ConsoleService`` whose ``token``/``client`` resolve from
    ``_request_token`` instead of being fixed at construction or mutated in
    place, so the one shared instance a console app builds is safe to use
    concurrently across requests bearing different Bearer tokens.
    """

    def __init__(self, server_url: str) -> None:
        # Deliberately not calling super().__init__(): it assigns
        # self.token/self.client as plain instance attributes, which would
        # shadow the properties below.
        self.server_url = server_url.rstrip("/")
        self._http: httpx.AsyncClient | None = None

    @property
    def token(self) -> str | None:
        return _request_token.get()

    @token.setter
    def token(self, value: str | None) -> None:
        # Setters exist only so this stays a writable property in mypy's eyes
        # (matching ConsoleService's plain attribute) -- nothing should ever
        # call this; direct assignment would silently defeat the whole point
        # of resolving the token from request context, so fail loudly.
        raise AttributeError(
            "_ScopedConsoleService.token is derived from _request_token; "
            "set _request_token instead of assigning to .token",
        )

    @property
    def client(self) -> FluxClient:
        return FluxClient(self.server_url, self.token)

    @client.setter
    def client(self, value: FluxClient) -> None:
        raise AttributeError(
            "_ScopedConsoleService.client is derived from _request_token; "
            "set _request_token instead of assigning to .client",
        )


def _error_detail(exc: httpx.HTTPStatusError):
    """Recover the server's structured error body, verbatim, for the client.

    Falls back to the raw text when the upstream response was not JSON, so
    the client always gets *something* for the detail field.
    """
    try:
        return exc.response.json()
    except ValueError:
        return exc.response.text or str(exc)


class ConsoleWriteState:
    """Tracks, per Bearer token, whether that token can write.

    Keyed by token (never a single process-global flag): in ``api`` mode
    different requests can carry genuinely different tokens, and a 403
    observed under one must never degrade the console for another. Each
    token's answer is resolved once -- via a real probe on first use (see
    ``_probe_can_write``), not merely by waiting for a real write to fail --
    then updated reactively (never back to True) if a later write for that
    same token is denied, e.g. because a standing grant was revoked
    mid-session.
    """

    def __init__(self) -> None:
        self._per_token: dict[str | None, bool] = {}

    def mark_forbidden(self, token: str | None) -> None:
        self._per_token[token] = False

    async def resolve(self, token: str | None, service: ConsoleService) -> bool:
        if token not in self._per_token:
            self._per_token[token] = await _probe_can_write(service)
        return self._per_token[token]


async def _probe_can_write(service: ConsoleService) -> bool:
    """Cheap, deterministic, side-effect-free probe of write authorization.

    ``GET /workflows/{namespace}/{workflow_name}/cancel/{execution_id}``
    (which ``ConsoleService.stop`` wraps) checks the caller's
    ``workflow:{namespace}:{workflow_name}:run`` permission *before* looking
    up whether ``execution_id`` exists (verified by reading
    ``flux/api/workflow_routes.py``'s cancel route: the authorization check
    precedes ``ContextManager.get``). So a "cancel" against a
    guaranteed-nonexistent execution id, under the namespace/workflow every
    console session runs in, is a real dry run of write authorization with
    no side effects: a structured 403 means the token lacks ``:run``;
    anything else (a 404 "not found" is what an authorized caller gets,
    since the fake id never exists) means it has at least this write grant.
    """
    try:
        await service.stop("__console_can_write_probe__", _NAMESPACE, "agent_chat")
    except httpx.HTTPStatusError as exc:
        # 403 is the permission-denied shape this probe targets; 401 means
        # the token isn't even authenticated, which is no more "can write"
        # than a bare permission gap -- everything else (404 "not found" is
        # the expected shape for an authorized caller here) means the write
        # check passed.
        return exc.response.status_code not in (401, 403)
    except Exception:
        # A network/etc. failure here shouldn't paint the console read-only
        # for an unrelated outage -- a real write attempt still gets the
        # correct, authoritative answer via `_call`.
        return True
    else:
        # Cancelling a fake execution id cannot genuinely succeed; treat an
        # unexpected 2xx defensively as "can write" (authorization clearly passed).
        return True


async def _call(
    write_state: ConsoleWriteState,
    coro: Awaitable[_T],
    *,
    is_write: bool = False,
) -> _T:
    """Run one ConsoleService/hub call, translating server errors into clean
    HTTPExceptions instead of 500 tracebacks.

    Every console endpoint that talks to the Flux server goes through here:
    an ``httpx.HTTPStatusError`` (e.g. a 403 with ``missing_permission``, or
    a plain 404) is re-raised with the same status and body; anything else
    (e.g. ``ConsoleService.spawn``'s bare ``RuntimeError`` when the server
    never reports an execution_id for a custom-workflow registration
    failure -- see Task 4's review note) becomes a 502 rather than an
    unhandled exception. ``is_write`` scopes the read-only-degradation
    signal to actual write attempts, so a 403 on a plain list/read call
    (a workflow-read gap, a different permission entirely) never falsely
    flips ``can_write`` -- and it is recorded against the *current request's*
    token, never every token.
    """
    try:
        return await coro
    except httpx.HTTPStatusError as exc:
        detail = _error_detail(exc)
        if is_write and exc.response.status_code == 403:
            write_state.mark_forbidden(_request_token.get())
        raise HTTPException(status_code=exc.response.status_code, detail=detail) from exc
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 -- translate, never let it 500-traceback
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def mount_console_routes(
    app: FastAPI,
    *,
    server_url: str,
    agent_name: str | None,
    token_dependency: Callable[..., str | None],
    csrf_dependency: Callable[..., None],
) -> None:
    """Register the /console/* routes on ``app``.

    ``token_dependency``/``csrf_dependency`` are the same dependency
    callables the caller (ApiUI) applies to its own routes, so auth and CSRF
    behavior is identical between the legacy single-agent routes and the
    console surface.
    """
    service = _ScopedConsoleService(server_url)
    hub = EventHub(service)
    write_state = ConsoleWriteState()
    # asyncio only weakly references a bare create_task() result; a detached
    # turn (client disconnected before log_delta) needs a strong reference
    # somewhere outside the generator frame or it can be GC'd mid-run.
    background_turns: set[asyncio.Task] = set()

    async def _service_dependency(token: str | None = Depends(token_dependency)) -> ConsoleService:
        # Must be `async def`: FastAPI dispatches sync dependencies through a
        # threadpool, which runs against a *copy* of the current context, so
        # a plain `def` here would set the contextvar somewhere the endpoint
        # body (and any hub-driven background task) never sees.
        _request_token.set(token)
        return service

    @app.get("/console/state")
    async def console_state(
        token: str | None = Depends(token_dependency),
        service: ConsoleService = Depends(_service_dependency),
    ) -> dict:
        return {
            "agent": agent_name,
            "session": None,
            "server_url": server_url,
            "can_write": await write_state.resolve(token, service),
        }

    @app.get("/console/agents")
    async def console_agents(
        service: ConsoleService = Depends(_service_dependency),
    ) -> list[dict]:
        return await _call(write_state, service.list_agents())

    @app.get("/console/sessions")
    async def console_sessions(
        agent: str | None = None,
        service: ConsoleService = Depends(_service_dependency),
    ) -> list[dict]:
        rows = await _call(write_state, service.list_sessions(agent))
        # derived_title comes ONLY from the hub's title cache (populated at
        # session open/turn boundaries) -- never a per-row detail fetch,
        # per the spec's cheap-list rule. Never-opened sessions get null.
        return [
            {
                "execution_id": row.execution_id,
                "agent_name": row.agent_name,
                "state": row.state,
                "name": row.name,
                "started_at": row.started_at,
                "workflow_name": row.workflow_name,
                "derived_title": hub.titles.get(row.execution_id),
            }
            for row in rows
        ]

    @app.get("/console/approvals")
    async def console_approvals(
        service: ConsoleService = Depends(_service_dependency),
    ) -> list[dict]:
        rows = await _call(write_state, service.list_approvals())
        return [
            {
                "execution_id": row.execution_id,
                "task_call_id": row.task_call_id,
                "task_name": row.task_name,
                "target_value": row.target_value,
                "requested_at": row.requested_at,
            }
            for row in rows
        ]

    @app.get("/console/sessions/{session_id}/detail")
    async def console_session_detail(
        session_id: str,
        service: ConsoleService = Depends(_service_dependency),
    ) -> dict:
        # open_session both returns the detail and caches its derived title
        # for the list endpoint above -- the "seen at open" half of the
        # title-cache contract.
        return await _call(write_state, hub.open_session(session_id))

    @app.post("/console/sessions", dependencies=[Depends(csrf_dependency)])
    async def console_create_session(
        body: dict = Body(default_factory=dict),
        service: ConsoleService = Depends(_service_dependency),
    ) -> dict:
        agent = body.get("agent")
        if not agent:
            raise HTTPException(status_code=400, detail="Missing required 'agent' field")
        name = body.get("name")
        execution_id = await _call(write_state, service.spawn(agent, name), is_write=True)
        return {"execution_id": execution_id}

    @app.post(
        "/console/sessions/{session_id}/send",
        dependencies=[Depends(csrf_dependency)],
    )
    async def console_send(
        session_id: str,
        body: dict = Body(default_factory=dict),
        service: ConsoleService = Depends(_service_dependency),
    ) -> EventSourceResponse:
        text = body.get("text", "")
        detail = await _call(write_state, hub.open_session(session_id))
        workflow_name = detail.get("workflow_name") or "agent_chat"
        queue = hub.subscribe()

        async def _frames():
            # agent_name is accepted by ConsoleService.send/EventHub.run_turn
            # for signature parity only -- neither actually reads it (see
            # ConsoleService.send's docstring) -- so there is nothing worth
            # fetching just to fill this in.
            task = asyncio.create_task(
                hub.run_turn(session_id, agent_name or "", workflow_name, text),
            )
            background_turns.add(task)
            task.add_done_callback(background_turns.discard)
            try:
                while True:
                    envelope = await queue.get()
                    if envelope.session_id != session_id:
                        continue
                    yield {
                        "data": json.dumps(
                            {"kind": envelope.event.kind, "data": envelope.event.data},
                        ),
                    }
                    if envelope.event.kind == KIND_LOG_DELTA:
                        break
            finally:
                hub.unsubscribe(queue)
                # Deliberately not awaited/cancelled here: run_turn never
                # raises (Task 5's contract) and other live subscribers
                # (a second tab, the TUI) still need its log_delta even if
                # *this* stream's client just disconnected -- background_turns
                # keeps it alive until it finishes on its own.

        return EventSourceResponse(_frames())

    @app.post(
        "/console/approvals/{execution_id}/{task_call_id:path}",
        dependencies=[Depends(csrf_dependency)],
    )
    async def console_decide(
        execution_id: str,
        task_call_id: str,
        body: dict = Body(default_factory=dict),
        service: ConsoleService = Depends(_service_dependency),
    ) -> dict:
        approve = body.get("approve")
        if not isinstance(approve, bool):
            raise HTTPException(
                status_code=400,
                detail="Decision requires a boolean 'approve' field.",
            )
        always = bool(body.get("always", False))
        always_for_target = bool(body.get("always_for_target", False))
        result = await _call(
            write_state,
            service.decide(execution_id, task_call_id, approve, always, always_for_target),
            is_write=True,
        )
        return {"result": result}

    @app.post(
        "/console/sessions/{session_id}/elicitation",
        dependencies=[Depends(csrf_dependency)],
    )
    async def console_elicitation(
        session_id: str,
        body: dict = Body(default_factory=dict),
        service: ConsoleService = Depends(_service_dependency),
    ) -> Response:
        payload = body.get("payload")
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Missing required 'payload' field")
        detail = await _call(write_state, hub.open_session(session_id))
        workflow_name = detail.get("workflow_name") or "agent_chat"
        await _call(
            write_state,
            service.respond_to_elicitation(session_id, workflow_name, payload),
            is_write=True,
        )
        return Response(status_code=204)

    @app.put(
        "/console/sessions/{session_id}/name",
        dependencies=[Depends(csrf_dependency)],
    )
    async def console_rename(
        session_id: str,
        body: dict = Body(default_factory=dict),
        service: ConsoleService = Depends(_service_dependency),
    ) -> dict:
        name = body.get("name")
        if not isinstance(name, str) or not name.strip():
            raise HTTPException(status_code=400, detail="Missing required 'name' field")
        name = name.strip()
        await _call(write_state, service.rename(session_id, name), is_write=True)
        return {"execution_id": session_id, "name": name}

    @app.post(
        "/console/sessions/{session_id}/stop",
        dependencies=[Depends(csrf_dependency)],
    )
    async def console_stop(
        session_id: str,
        service: ConsoleService = Depends(_service_dependency),
    ) -> dict:
        detail = await _call(write_state, hub.open_session(session_id))
        workflow_name = detail.get("workflow_name") or "agent_chat"
        await _call(
            write_state,
            service.stop(session_id, _NAMESPACE, workflow_name),
            is_write=True,
        )
        return {"execution_id": session_id, "stopped": True}
