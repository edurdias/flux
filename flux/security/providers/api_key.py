from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Callable

from flux.security.identity import FluxIdentity
from flux.security.models import APIKeyModel, ServiceAccountModel
from flux.security.principals import PrincipalModel, PrincipalRoleModel
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
            if key_model.expires_at and key_model.expires_at < datetime.now(timezone.utc):
                logger.warning(f"API key '{key_model.name}' has expired")
                return None
            principal = session.query(PrincipalModel).filter_by(id=key_model.principal_id).first()
            if not principal or not principal.enabled:
                return None
            role_rows = session.query(PrincipalRoleModel).filter_by(principal_id=principal.id).all()
            if role_rows:
                roles = frozenset(r.role_name for r in role_rows)
            else:
                sa = session.query(ServiceAccountModel).filter_by(name=principal.subject).first()
                roles = frozenset(sa.roles) if sa else frozenset()
            return FluxIdentity(
                subject=principal.subject,
                roles=roles,
                metadata={"token_type": "api_key", "key_name": key_model.name},
            )
        finally:
            session.close()
