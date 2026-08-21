"""ConsoleService — the single server client for console surfaces.

Wraps ``FluxClient`` (agent-execution streaming: start/resume/decide) and
adds the plain REST calls console surfaces need on top of it (session and
approval listings, rename, cancel, custom-workflow registration), so every
console renderer (web app, TUI, api mode) talks to the Flux server through
one place instead of each re-implementing HTTP plumbing.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx

from flux.agents.flux_client import FluxClient


@dataclass(frozen=True)
class SessionPage:
    """A page of rail rows plus the size of the full set behind it."""

    rows: list[SessionRow]
    total: int

    @property
    def truncated(self) -> bool:
        return self.total > len(self.rows)


@dataclass(frozen=True)
class SessionRow:
    execution_id: str
    agent_name: str
    state: str
    name: str | None
    started_at: str | None
    workflow_name: str
    derived_title: str | None = None  # display fallback, never persisted


@dataclass(frozen=True)
class ApprovalRow:
    execution_id: str
    task_call_id: str
    task_name: str
    target_value: str | None
    requested_at: str | None


class ConsoleService:
    """Single server client for console surfaces (web app, TUI, api mode).

    Agent-execution streaming (start/resume/decide) goes through the shared
    ``FluxClient``; the console-specific REST calls on top of it (listings,
    rename, cancel, custom-workflow registration) are made directly here,
    over one pooled ``httpx.AsyncClient`` the caller releases via
    :meth:`aclose`.
    """

    def __init__(self, server_url: str, token: str | None) -> None:
        self.server_url = server_url.rstrip("/")
        self.token = token
        self.client = FluxClient(server_url, token)
        self._http: httpx.AsyncClient | None = None

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _ensure_http(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient()
        return self._http

    async def list_agents(self) -> list[dict]:
        http = self._ensure_http()
        response = await http.get(f"{self.server_url}/admin/agents", headers=self._headers())
        response.raise_for_status()
        return response.json()

    async def list_sessions(
        self,
        agent: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> SessionPage:
        """One page of sessions, and how many there are in total.

        The server caps this listing (``limit`` defaults to 50). Returning
        only the rows made that cap invisible: a rail showing fifty of two
        hundred sessions looks exactly like a rail showing all of them, and
        the session an operator is hunting for is simply absent (#245). A
        caller can widen the page with ``limit``/``offset`` (both consoles
        do so in their "load the rest" control); ``total`` still counts the
        full set either way.
        """
        params: dict[str, int | str] = {}
        if agent:
            params["agent"] = agent
        if limit is not None:
            params["limit"] = limit
        if offset:
            params["offset"] = offset
        http = self._ensure_http()
        response = await http.get(
            f"{self.server_url}/agents/sessions",
            params=params,
            headers=self._headers(),
        )
        response.raise_for_status()
        body = response.json()
        rows = [
            SessionRow(
                execution_id=row["execution_id"],
                agent_name=row["agent_name"],
                state=row["state"],
                name=row.get("name"),
                started_at=row.get("started_at"),
                workflow_name=row["workflow_name"],
            )
            for row in body.get("sessions", [])
        ]
        return SessionPage(rows=rows, total=int(body.get("total", len(rows))))

    async def list_approvals(self) -> list[ApprovalRow]:
        http = self._ensure_http()
        response = await http.get(f"{self.server_url}/approvals", headers=self._headers())
        response.raise_for_status()
        body = response.json()
        return [
            ApprovalRow(
                execution_id=row["execution_id"],
                task_call_id=row["task_call_id"],
                task_name=row["task_name"],
                target_value=row.get("target_value"),
                requested_at=row.get("requested_at"),
            )
            for row in body.get("approvals", [])
        ]

    async def get_detail(self, execution_id: str) -> dict:
        return await self.client.get_execution(execution_id, detailed=True)

    async def spawn(self, agent_name: str, name: str | None) -> str:
        """Start a session for ``agent_name`` and return its execution_id.

        Replicates the custom-workflow decision `flux/cli.py`'s `agent
        start` command makes (cli.py:3057-3088 as of this writing): an agent
        declaring `workflow_file` runs under a dedicated
        `agent_custom_<name>` workflow, registered here first so the run
        below can target it by name; every other agent runs the shared
        `agent_chat` workflow. Never unconditionally `agent_chat`.
        """
        agent_def = await self.client.get_agent(agent_name)
        workflow_name = "agent_chat"
        workflow_file = agent_def.get("workflow_file")
        if workflow_file:
            workflow_name = f"agent_custom_{agent_name}"
            source = (
                workflow_file.encode("utf-8") if isinstance(workflow_file, str) else workflow_file
            )
            await self._register_workflow(workflow_name, source)

        execution_id: str | None = None
        async for eid, _ in self.client.start_agent(
            agent_name,
            namespace="agents",
            workflow_name=workflow_name,
            name=name,
        ):
            if eid is not None:
                execution_id = eid
        if execution_id is None:
            raise RuntimeError(
                f"spawn: server never reported an execution_id for agent {agent_name!r}",
            )
        return execution_id

    async def _register_workflow(self, workflow_name: str, source: bytes) -> None:
        http = self._ensure_http()
        # Multipart upload: drop the JSON Content-Type so httpx sets its own
        # multipart boundary header (same exclusion flux.cli's agent-start
        # registration and FluxClient.ensure_workflow_registered both make).
        headers = {k: v for k, v in self._headers().items() if k != "Content-Type"}
        response = await http.post(
            f"{self.server_url}/workflows",
            files={"file": (f"{workflow_name}.py", source)},
            headers=headers,
        )
        response.raise_for_status()

    async def send(
        self,
        execution_id: str,
        agent_name: str,
        workflow_name: str,
        text: str,
    ) -> AsyncIterator[dict]:
        """Resume ``execution_id`` with a user message and yield raw events.

        Takes the session's own ``workflow_name`` (from its `SessionRow`) so
        custom-workflow sessions resume `agent_custom_<name>`, never the
        default. ``agent_name`` is accepted for parity with the caller's
        session record; the resume route is workflow-scoped and does not
        need it.
        """
        async for event in self.client.resume(
            execution_id,
            message=text,
            namespace="agents",
            workflow_name=workflow_name,
        ):
            yield event

    async def respond_to_elicitation(
        self,
        execution_id: str,
        workflow_name: str,
        payload: dict,
    ) -> None:
        """Resume a paused execution with an elicitation response.

        Per-session: resumes the caller's own execution+workflow, unlike the
        legacy `/elicitation` route, which resumes with process-level names
        and is wrong for picker-spawned sessions.
        """
        async for _ in self.client.resume(
            execution_id,
            payload=payload,
            namespace="agents",
            workflow_name=workflow_name,
        ):
            pass

    async def decide(
        self,
        execution_id: str,
        task_call_id: str,
        approve: bool,
        always: bool = False,
        always_for_target: bool = False,
    ) -> str:
        body = await self.client.decide_approval(
            execution_id,
            task_call_id,
            approved=approve,
            always=always,
            always_for_target=always_for_target,
        )
        # FluxClient.decide_approval swallows the 409 and returns the body;
        # already-decided is keyed off the body, never the status code.
        if body.get("error") == "already_decided":
            return "already_decided"
        return "decided"

    async def rename(self, execution_id: str, name: str) -> None:
        http = self._ensure_http()
        response = await http.put(
            f"{self.server_url}/executions/{execution_id}/name",
            json={"name": name},
            headers=self._headers(),
        )
        response.raise_for_status()

    async def stop(self, execution_id: str, namespace: str, workflow_name: str) -> None:
        # POST /executions/{id}/cancel does not exist on the server; this is
        # the working, workflow-scoped cancel route.
        http = self._ensure_http()
        response = await http.get(
            f"{self.server_url}/workflows/{namespace}/{workflow_name}/cancel/{execution_id}",
            headers=self._headers(),
        )
        response.raise_for_status()

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None
