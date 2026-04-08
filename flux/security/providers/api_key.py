from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Callable

from flux.security.identity import FluxIdentity
from flux.security.models import APIKeyModel
from flux.security.providers import AuthProvider
from flux.utils import get_logger

logger = get_logger(__name__)


class APIKeyProvider(AuthProvider):
    def __init__(self, session_factory: Callable):
        self._session_factory = session_factory

    async def authenticate(self, token: str) -> FluxIdentity | None:
        key_hash = hashlib.sha256(token.encode()).hexdigest()
        session = self._session_factory()
        try:
            key_model = session.query(APIKeyModel).filter(APIKeyModel.key_hash == key_hash).first()
            if not key_model:
                return None
            if key_model.expires_at and key_model.expires_at < datetime.now():
                logger.warning(f"API key '{key_model.name}' has expired")
                return None
            sa = key_model.service_account
            return FluxIdentity(
                subject=sa.name,
                roles=frozenset(sa.roles),
                metadata={"token_type": "api_key", "key_name": key_model.name},
            )
        finally:
            session.close()
