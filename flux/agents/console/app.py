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
import contextlib
import contextvars
import hashlib
import json
import secrets
import time
from collections import OrderedDict
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar

import httpx
from fastapi import Body, Depends, FastAPI, HTTPException, Response
from sse_starlette.sse import EventSourceResponse

from flux.agents.console.errors import error_detail, missing_permission_of
from flux.agents.console.hub import KIND_LOG_DELTA, EventHub
from flux.agents.console.service import ConsoleService
from flux.agents.flux_client import FluxClient

__all__ = ["ConsoleWriteState", "missing_permission_of", "mount_console_routes"]

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

# The FluxClient built for the token above, cached for the life of the
# context that built it (one request, or one detached turn) so a call
# reading ``.client`` several times does not build several. Stored with its
# own token so a context that never set one of its own -- or that outlives a
# token change -- can never be handed another request's client.
_request_client: contextvars.ContextVar[tuple[str | None, FluxClient] | None] = (
    contextvars.ContextVar("console_request_client", default=None)
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
        token = self.token
        cached = _request_client.get()
        if cached is not None and cached[0] == token:
            return cached[1]
        client = FluxClient(self.server_url, token)
        _request_client.set((token, client))
        return client

    @client.setter
    def client(self, value: FluxClient) -> None:
        raise AttributeError(
            "_ScopedConsoleService.client is derived from _request_token; "
            "set _request_token instead of assigning to .client",
        )


_WRITE_STATE_MAX_ENTRIES = 256
_WRITE_STATE_TTL_SECONDS = 900.0


@dataclass
class _WriteAnswer:
    can_write: bool
    missing: str | None
    stored_at: float


class ConsoleWriteState:
    """Tracks, per Bearer token, whether that token can write -- and, when it
    cannot, which permission it is missing.

    Keyed by token (never a single process-global flag): in ``api`` mode
    different requests can carry genuinely different tokens, and a 403
    observed under one must never degrade the console for another. Each
    token's answer is resolved once -- via a real probe on first use (see
    ``_probe_can_write``), not merely by waiting for a real write to fail --
    then updated reactively (never back to True) if a later write for that
    same token is denied, e.g. because a standing grant was revoked
    mid-session.

    The permission name travels with the answer because a read-only console
    disables every write control, so no later 403 can ever arrive to name it:
    the probe's own denial is the *only* chance to learn it.

    What is keyed is a salted hash of the token, not the token: api mode
    admits an unbounded number of live credentials, and a process-lifetime
    map of them is a credential store nobody asked for -- one heap dump or
    one repr() away from leaking every operator's token (#245). The salt is
    generated per instance, so a digest means nothing to anything but the
    cache that produced it.

    The map is bounded and each answer expires: oldest-first eviction past
    ``max_entries`` (``0`` remembers nothing and re-probes every read), and
    an answer older than ``ttl_seconds`` is re-probed
    rather than believed forever. Expiry is why "never back to True" is
    scoped to the TTL -- within it a denial stands, past it a grant issued
    mid-session is picked up, which is the same reason the cache exists at
    all rather than probing every read.
    """

    def __init__(
        self,
        *,
        max_entries: int = _WRITE_STATE_MAX_ENTRIES,
        ttl_seconds: float = _WRITE_STATE_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_entries < 0:
            # Not a smaller cache: eviction would pop an already-empty map,
            # surfacing as a KeyError from an unrelated later write. Zero is
            # allowed and means "remember nothing".
            raise ValueError(f"max_entries must be >= 0, got {max_entries}")
        self._answers: OrderedDict[str, _WriteAnswer] = OrderedDict()
        self._max_entries = max_entries
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        # Per-process, so a digest never survives the process that made it
        # and cannot be precomputed against a known token.
        self._salt = secrets.token_bytes(16)

    def __len__(self) -> int:
        return len(self._answers)

    def _key(self, token: str | None) -> str:
        # None (web mode's browser request, which carries no credential of
        # its own) and "" both mean "no token" and share a key by design.
        return hashlib.blake2b(
            (token or "").encode(),
            key=self._salt,
            digest_size=16,
        ).hexdigest()

    def _live(self, key: str) -> _WriteAnswer | None:
        answer = self._answers.get(key)
        if answer is None:
            return None
        if self._clock() - answer.stored_at >= self._ttl_seconds:
            del self._answers[key]
            return None
        return answer

    def _store(self, key: str, can_write: bool, missing: str | None) -> None:
        self._answers[key] = _WriteAnswer(can_write, missing, self._clock())
        self._answers.move_to_end(key)
        while len(self._answers) > self._max_entries:
            self._answers.popitem(last=False)

    def mark_forbidden(self, token: str | None, permission: str | None = None) -> None:
        key = self._key(token)
        known = self._live(key)
        # Never overwrite a known permission with None: a later denial whose
        # body did not name one must not erase what the probe already learned.
        if permission is None and known is not None:
            permission = known.missing
        self._store(key, False, permission)

    async def resolve(
        self,
        token: str | None,
        service: ConsoleService,
        workflow_name: str = "agent_chat",
    ) -> bool:
        key = self._key(token)
        known = self._live(key)
        if known is not None:
            return known.can_write
        can_write, permission, answered = await _probe_can_write(service, workflow_name)
        # A probe that never reached the server answers nothing: caching its
        # degrade-open "yes" would leave a genuinely read-only operator
        # looking at enabled write controls for the life of the process,
        # since nothing ever re-probes. Retried on the next read instead.
        if answered:
            self._store(key, can_write, None if can_write else permission)
        return can_write

    def missing_permission(self, token: str | None) -> str | None:
        known = self._live(self._key(token))
        return known.missing if known is not None else None


async def _probe_can_write(
    service: ConsoleService,
    workflow_name: str = "agent_chat",
) -> tuple[bool, str | None, bool]:
    """Cheap, deterministic, side-effect-free probe of write authorization.

    Returns ``(can_write, missing_permission, answered)``. The second element
    is the permission the denial named, which the console surfaces so a
    read-only session can say *what* it is missing instead of merely that it
    is read-only. The third says whether the server actually answered: a
    probe that failed in transit degrades open but must not be remembered
    (see ``ConsoleWriteState.resolve``).

    The workflow named is the one this console process runs, not a
    constant: the check is per workflow, so a console started on a custom
    workflow used to probe ``agent_chat`` -- a permission its operator has
    no reason to hold -- and painted itself read-only until a real write
    proved otherwise (#245). Sessions the console spawns for an agent that
    declares its own ``workflow_file`` still run ``agent_custom_<agent>``
    and are not covered by this one process-wide answer; that case remains
    self-correcting on the first real write.

    ``GET /workflows/{namespace}/{workflow_name}/cancel/{execution_id}``
    (which ``ConsoleService.stop`` wraps) checks the caller's
    ``workflow:{namespace}:{workflow_name}:run`` permission *before* looking
    up whether ``execution_id`` exists (verified by reading
    ``flux/api/workflow_routes.py``'s cancel route: the authorization check
    precedes ``ContextManager.get``). So a "cancel" against a
    guaranteed-nonexistent execution id, under the namespace/workflow every
    console session runs in, is a real dry run of write authorization with
    no side effects: a 403 means the token lacks ``:run``; anything else (a
    404 "not found" is what an authorized caller gets, since the fake id
    never exists) means it has at least this write grant.
    """
    try:
        await service.stop("__console_can_write_probe__", _NAMESPACE, workflow_name)
    except httpx.HTTPStatusError as exc:
        # 403 is the permission-denied shape this probe targets; 401 means
        # the token isn't even authenticated, which is no more "can write"
        # than a bare permission gap -- everything else (404 "not found" is
        # the expected shape for an authorized caller here) means the write
        # check passed.
        if exc.response.status_code not in (401, 403):
            return True, None, True
        return False, missing_permission_of(error_detail(exc)), True
    except Exception:
        # A network/etc. failure here shouldn't paint the console read-only
        # for an unrelated outage -- a real write attempt still gets the
        # correct, authoritative answer via `_call`.
        return True, None, False
    else:
        # Cancelling a fake execution id cannot genuinely succeed; treat an
        # unexpected 2xx defensively as "can write" (authorization clearly passed).
        return True, None, True


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
        detail = error_detail(exc)
        if is_write and exc.response.status_code == 403:
            write_state.mark_forbidden(_request_token.get(), missing_permission_of(detail))
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
    session_id: str | None = None,
    workflow_name: str = "agent_chat",
) -> None:
    """Register the /console/* routes on ``app``.

    ``token_dependency``/``csrf_dependency`` are the same dependency
    callables the caller (ApiUI) applies to its own routes, so auth and CSRF
    behavior is identical between the legacy single-agent routes and the
    console surface. ``session_id`` is the session the process was started
    on (``flux agent start --session <id>``), reported by ``/console/state``
    because the page has nowhere else to learn it.
    """
    service = _ScopedConsoleService(server_url)
    hub = EventHub(service)
    write_state = ConsoleWriteState()
    # The hub is built here, but the app is what callers hold: WebUI, the
    # TUI and tests all reach it through the app they mounted onto.
    app.state.console_hub = hub

    # The service owns a pooled httpx client; the TUI path closes it in its
    # own `finally`, so the served path has to close it too. Wrapping the
    # router's lifespan (rather than the removed on_event/add_event_handler
    # hooks) keeps whatever lifespan the app already had.
    previous_lifespan = app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with previous_lifespan(app):
            try:
                yield
            finally:
                await service.aclose()

    app.router.lifespan_context = _lifespan
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
        can_write = await write_state.resolve(token, service, workflow_name)
        return {
            "agent": agent_name,
            "session": session_id,
            "server_url": server_url,
            "can_write": can_write,
            # Named here or nowhere: a read-only console disables every write
            # control, so it can never provoke a 403 of its own to learn it.
            "missing_permission": None if can_write else write_state.missing_permission(token),
        }

    @app.get("/console/agents")
    async def console_agents(
        service: ConsoleService = Depends(_service_dependency),
    ) -> list[dict]:
        rows = await _call(write_state, service.list_agents())
        # Projected, never relayed verbatim: /admin/agents returns the whole
        # definition, including workflow_file/tools_file source and
        # mcp_servers blocks that can carry credentials. The pickers on both
        # surfaces show name · model · description and nothing else, so
        # that is all that reaches a browser.
        return [
            {
                "name": row.get("name"),
                "model": row.get("model"),
                "description": row.get("description"),
            }
            for row in rows
            if isinstance(row, dict)
        ]

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
        # Refused before the stream opens: two turns for one session
        # interleave their frames into every subscriber and race the same
        # execution, and the server's own non-PAUSED rejection lands only
        # after the second turn has started streaming. This pre-check is
        # what produces the 409, and it is the whole answer for requests
        # that do not overlap. Two arriving together can both pass it; the
        # hub's own check when the turn starts is what stops the second
        # from running, reporting it as an error event on the stream this
        # one already opened.
        if hub.turn_in_flight(session_id):
            raise HTTPException(
                status_code=409,
                detail=f"A turn is already running for session {session_id}",
            )
        detail = await _call(write_state, hub.open_session(session_id))
        # The session's own workflow, deliberately shadowing the
        # process-wide one this module was mounted with: a session
        # spawned for an agent that declares workflow_file runs
        # agent_custom_<agent>, and resuming it under anything else is a
        # different workflow entirely.
        workflow_name = detail.get("workflow_name") or "agent_chat"

        async def _frames():
            # Subscribed here rather than in the endpoint body: the matching
            # unsubscribe lives in this generator's `finally`, so a client
            # that hangs up before the generator is ever resumed would leave
            # a queue in the hub that nobody drains and every session's
            # events keep filling.
            queue = hub.subscribe()
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
                            {
                                # The session id rides on every frame, not
                                # just the stream's subscription: a browser
                                # that switched sessions mid-turn is still
                                # draining this response, and without it the
                                # reducer cannot tell a straggler frame from
                                # one belonging to the session now on screen.
                                "session_id": envelope.session_id,
                                "kind": envelope.event.kind,
                                "data": envelope.event.data,
                            },
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
