from __future__ import annotations

import asyncio
import time

import httpx
import jwt
from jwt import PyJWKClient

from flux.security.config import OIDCConfig
from flux.security.errors import AuthenticationError
from flux.security.identity import FluxIdentity
from flux.security.providers import AuthProvider
from flux.utils import get_logger

logger = get_logger(__name__)

_EXCLUDED_METADATA_CLAIMS = frozenset(
    {
        "email",
        "email_verified",
        "sub",
        "iss",
        "aud",
        "exp",
        "iat",
        "jti",
        "azp",
        "nonce",
        "at_hash",
        "auth_time",
        "session_state",
        "acr",
        "amr",
        "realm_access",
        "resource_access",
        "typ",
    },
)


class OIDCProvider(AuthProvider):
    def __init__(self, config: OIDCConfig, registry=None):
        self.config = config
        self._registry = registry
        self._discovery: dict | None = None
        self._discovery_fetched_at: float = 0
        self._jwks_client: PyJWKClient | None = None

    async def _ensure_discovery(self):
        if (
            self._discovery
            and (time.monotonic() - self._discovery_fetched_at) < self.config.jwks_cache_ttl
        ):
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
        assert self._jwks_client is not None
        signing_key = await asyncio.to_thread(self._jwks_client.get_signing_key_from_jwt, token)
        return signing_key.key

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
                options={"require": ["exp", "iss", "sub", "aud"]},
            )
            subject = payload["sub"]
            issuer = payload["iss"]
            return await self._resolve_principal(subject, issuer, payload)
        except AuthenticationError:
            raise
        except jwt.ExpiredSignatureError:
            logger.warning("OIDC token expired")
            return None
        except jwt.InvalidTokenError as e:
            logger.warning(f"OIDC token validation failed: {e}")
            return None
        except Exception as e:
            logger.error(f"OIDC provider error: {type(e).__name__}: {e}")
            return None

    async def _resolve_principal(self, subject: str, issuer: str, claims: dict) -> FluxIdentity:
        if self._registry is None:
            return FluxIdentity(
                subject=subject,
                roles=frozenset(),
                metadata={"token_type": "oidc", "issuer": issuer},
            )

        principal = self._registry.find(subject, issuer)

        if principal is not None:
            if not principal.enabled:
                raise AuthenticationError("Principal disabled")
            self._registry.update_last_seen(principal.id)
            display_name = claims.get("name") or principal.display_name
            metadata = self._extract_metadata(claims)
            self._registry.update_metadata(
                principal.id,
                display_name=display_name,
                metadata=metadata,
            )
            roles = frozenset(self._registry.get_roles(principal.id))
            return FluxIdentity(
                subject=subject,
                roles=roles,
                metadata={
                    "token_type": "oidc",
                    "issuer": issuer,
                    "principal_id": principal.id,
                },
            )

        return await self._auto_provision(subject, issuer, claims)

    async def _auto_provision(self, subject: str, issuer: str, claims: dict) -> FluxIdentity:
        default_roles = getattr(self.config, "default_user_roles", [])
        if not default_roles:
            raise AuthenticationError("Principal not provisioned")

        display_name = claims.get("name", subject)
        metadata = self._extract_metadata(claims)

        try:
            principal = self._registry.create(
                type="user",
                subject=subject,
                external_issuer=issuer,
                display_name=display_name,
                metadata=metadata,
                enabled=True,
            )
            for role_name in default_roles:
                self._registry.assign_role(
                    principal.id,
                    role_name,
                    assigned_by="auto-provisioning",
                )
            logger.info(
                f"Auto-provisioned principal {subject} with roles {default_roles}",
            )
        except Exception as e:
            if "UNIQUE" in str(e).upper():
                principal = self._registry.find(subject, issuer)
                if principal is None:
                    raise AuthenticationError("Principal provisioning race failed") from e
            else:
                raise

        roles = frozenset(self._registry.get_roles(principal.id))
        return FluxIdentity(
            subject=subject,
            roles=roles,
            metadata={
                "token_type": "oidc",
                "issuer": issuer,
                "principal_id": principal.id,
            },
        )

    @staticmethod
    def _extract_metadata(claims: dict) -> dict:
        return {k: v for k, v in claims.items() if k not in _EXCLUDED_METADATA_CLAIMS}
