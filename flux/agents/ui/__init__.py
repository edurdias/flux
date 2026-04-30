from __future__ import annotations

from abc import ABC, abstractmethod

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
    async def display_reasoning(self, text: str) -> None:
        ...

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
