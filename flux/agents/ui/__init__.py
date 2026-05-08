from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from flux.agents.types import SessionEndOutput


class UI(ABC):
    @abstractmethod
    async def display_response(self, content: str | None) -> None:
        raise NotImplementedError()

    @abstractmethod
    async def display_tool_start(self, tool_id: str, name: str, args: dict) -> None:
        raise NotImplementedError()

    @abstractmethod
    async def display_tool_done(self, tool_id: str, name: str, status: str) -> None:
        raise NotImplementedError()

    @abstractmethod
    async def display_token(self, text: str) -> None:
        raise NotImplementedError()

    @abstractmethod
    async def display_reasoning(self, text: str) -> None: ...

    @abstractmethod
    async def display_elicitation(self, request: dict) -> dict:
        raise NotImplementedError()

    @abstractmethod
    async def prompt_user(self) -> str:
        raise NotImplementedError()

    async def begin_reply(self) -> None:
        pass

    async def end_reply(self) -> None:
        pass

    @abstractmethod
    async def display_session_info(self, session_id: str, agent_name: str) -> None:
        raise NotImplementedError()

    @abstractmethod
    async def display_session_end(self, output: SessionEndOutput) -> None:
        raise NotImplementedError()

    async def display_approval_request(self, request: dict[str, Any]) -> dict[str, Any]:
        """Render an approval prompt and return the operator's decision.

        Returns one of:
          - {"approved": bool, "reason": str|None, "always_approve": bool}
            — the dispatcher will POST this decision to the Flux server.
          - {"defer": True}
            — the UI declines to decide here (api/web modes); the consumer
            of the SSE stream is expected to call the approve/reject HTTP
            routes on the Flux server directly.

        Default implementation defers, so non-interactive UIs that do not
        override this method never auto-approve or auto-reject.
        """
        return {"defer": True}
