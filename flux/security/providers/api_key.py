from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from collections.abc import Callable

from flux.security.identity import FluxIdentity
from flux.security.models import APIKeyModel
from flux.security.providers import AuthProvider
from flux.utils import get_logger

logger = get_logger(__name__)


class APIKeyProvider(AuthProvider):
    def __init__(self, session_factory: Callable, registry=None):
        self._session_factory = session_factory
        self._registry = registry

    async def authenticate(self, token: str) -> FluxIdentity | None:
        key_hash = hashlib.sha256(token.encode()).hexdigest()
        session = self._session_factory()
        try:
            key_model = session.query(APIKeyModel).filter(APIKeyModel.key_hash == key_hash).first()
            if not key_model:
                return None
            if key_model.expires_at and key_model.expires_at.replace(
                tzinfo=timezone.utc,
            ) < datetime.now(timezone.utc):
                logger.warning(f"API key '{key_model.name}' has expired")
                return None

            if self._registry is None:
                logger.error("APIKeyProvider has no registry — cannot resolve principal")
                return None

            principal = self._registry.get(key_model.principal_id)
            if principal is None:
                logger.warning(
                    f"API key '{key_model.name}' references unknown principal '{key_model.principal_id}'",
                )
                return None

            if principal.type != "service_account":
                logger.warning(
                    f"API key '{key_model.name}' references principal of type '{principal.type}'; expected 'service_account'",
                )
                return None

            if not principal.enabled:
                logger.warning(f"Principal '{principal.subject}' is disabled")
                return None

            self._registry.update_last_seen(principal.id)
            roles = frozenset(self._registry.get_roles(principal.id))

            return FluxIdentity(
                subject=principal.subject,
                roles=roles,
                metadata={
                    "token_type": "api_key",
                    "issuer": "flux",
                    "principal_id": principal.id,
                    "key_name": key_model.name,
                },
            )
        finally:
            session.close()
