from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from flux.agents.events import AgentEvent, parse_event
from flux.agents.flux_client import FluxClient


class AgentSession:
    """Stateful wrapper around FluxClient that yields parsed AgentEvents.

    A session represents one Flux execution of the agent_chat workflow.
    Use start() for a new session or pass session_id to resume an existing one.
    """

    def __init__(
        self,
        client: FluxClient,
        agent_name: str,
        session_id: str | None = None,
        namespace: str = "agents",
        workflow_name: str = "agent_chat",
    ) -> None:
        self.client = client
        self.agent_name = agent_name
        self.session_id = session_id
        self.namespace = namespace
        self.workflow_name = workflow_name

    async def start(self) -> AsyncIterator[AgentEvent]:
        """Begin a new Flux execution and yield parsed events. Raises if already started."""
        if self.session_id is not None:
            raise RuntimeError("Session already started; use send() to continue")
        async for execution_id, raw in self.client.start_agent(
            self.agent_name,
            namespace=self.namespace,
            workflow_name=self.workflow_name,
        ):
            if execution_id is not None:
                self.session_id = execution_id
            for event in parse_event(raw):
                yield event

    async def send(self, message: str) -> AsyncIterator[AgentEvent]:
        """Resume the paused session with a user message. Requires start() first."""
        if self.session_id is None:
            raise RuntimeError("Session not started; call start() first")
        async for raw in self.client.resume(
            self.session_id,
            message=message,
            namespace=self.namespace,
            workflow_name=self.workflow_name,
        ):
            for event in parse_event(raw):
                yield event

    async def respond_to_elicitation(
        self,
        payload: dict[str, Any],
    ) -> AsyncIterator[AgentEvent]:
        """Resume the paused session with an elicitation response. Requires start() first."""
        if self.session_id is None:
            raise RuntimeError("Session not started; call start() first")
        async for raw in self.client.resume(
            self.session_id,
            payload=payload,
            namespace=self.namespace,
            workflow_name=self.workflow_name,
        ):
            for event in parse_event(raw):
                yield event
