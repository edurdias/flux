from __future__ import annotations

from abc import ABC, abstractmethod

from flux.security.identity import FluxIdentity


class AuthProvider(ABC):
    @abstractmethod
    async def authenticate(self, token: str) -> FluxIdentity | None:
        raise NotImplementedError()
