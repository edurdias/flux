from __future__ import annotations

import time

import httpx
import jwt
from jwt import PyJWKClient

from flux.security.config import OIDCConfig
from flux.security.identity import FluxIdentity
from flux.security.providers import AuthProvider
from flux.utils import get_logger

logger = get_logger(__name__)


class OIDCProvider(AuthProvider):
    def __init__(self, config: OIDCConfig):
        self.config = config
        self._discovery: dict | None = None
        self._discovery_fetched_at: float = 0
        self._jwks_client: PyJWKClient | None = None

    async def _ensure_discovery(self):
        if self._discovery and (time.monotonic() - self._discovery_fetched_at) < self.config.jwks_cache_ttl:
            return
        discovery_url = f"{self.config.issuer.rstrip('/')}/.well-known/openid-configuration"
        async with httpx.AsyncClient() as client:
            resp = await client.get(discovery_url)
            resp.raise_for_status()
            self._discovery = resp.json()
            self._discovery_fetched_at = time.monotonic()
        self._jwks_client = PyJWKClient(
            self._discovery["jwks_uri"],
            cache_jwk_set=True,
            lifespan=self.config.jwks_cache_ttl,
        )

    async def _get_signing_key(self, token: str):
        await self._ensure_discovery()
        return self._jwks_client.get_signing_key_from_jwt(token).key

    async def authenticate(self, token: str) -> FluxIdentity | None:
        try:
            signing_key = await self._get_signing_key(token)
            payload = jwt.decode(
                token,
                signing_key,
                algorithms=["RS256", "ES256"],
                issuer=self.config.issuer,
                audience=self.config.audience,
                leeway=self.config.clock_skew,
                options={"require": ["exp", "iss", "sub"]},
            )
            roles = set(self._resolve_claim(payload, self.config.roles_claim) or [])
            return FluxIdentity(
                subject=payload["sub"],
                roles=frozenset(roles),
                metadata={
                    "email": payload.get("email"),
                    "name": payload.get("name"),
                    "token_type": "oidc",
                },
            )
        except jwt.ExpiredSignatureError:
            logger.warning("OIDC token expired")
            return None
        except jwt.InvalidTokenError as e:
            logger.warning(f"OIDC token validation failed: {e}")
            return None

    @staticmethod
    def _resolve_claim(payload: dict, claim_path: str) -> list | None:
        parts = claim_path.split(".")
        current = payload
        for part in parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return None
        return current if isinstance(current, list) else None
