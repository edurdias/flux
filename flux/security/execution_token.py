from __future__ import annotations

import os
import secrets
import time

import jwt

from flux.security.identity import FluxIdentity
from flux.security.providers import AuthProvider
from flux.utils import get_logger

logger = get_logger(__name__)

EXECUTION_TOKEN_ISSUER = "flux-server"
EXECUTION_TOKEN_SCOPE = "execution"

_DEFAULT_EXECUTION_TOKEN_TTL = 604800

_ephemeral_secret: str | None = None


def _get_execution_token_secret() -> str:
    secret = os.environ.get("FLUX_EXECUTION_TOKEN_SECRET")
    if secret:
        return secret

    from flux.config import Configuration

    settings = Configuration.get().settings
    security_cfg = getattr(settings, "security", None)
    if security_cfg is not None:
        token_secret = getattr(security_cfg, "execution_token_secret", None)
        if token_secret:
            return token_secret

    debug_mode = getattr(settings, "debug", False)
    if debug_mode:
        global _ephemeral_secret
        if _ephemeral_secret is None:
            logger.warning(
                "execution_token_secret is not configured — auto-generating ephemeral secret. "
                "This is only safe for development. Set security.execution_token_secret in production.",
            )
            _ephemeral_secret = secrets.token_hex(32)
        return _ephemeral_secret

    raise RuntimeError(
        "execution_token_secret is not configured. "
        "Set FLUX_EXECUTION_TOKEN_SECRET env var or security.execution_token_secret in flux.toml.",
    )


def _get_execution_token_ttl() -> int:
    from flux.config import Configuration

    settings = Configuration.get().settings
    security_cfg = getattr(settings, "security", None)
    if security_cfg is not None:
        ttl = getattr(security_cfg, "execution_token_ttl", None)
        if ttl:
            return int(ttl)
    return _DEFAULT_EXECUTION_TOKEN_TTL


def mint_execution_token(
    subject: str,
    principal_issuer: str,
    execution_id: str,
    on_behalf_of: str,
    ttl_seconds: int | None = None,
) -> str:
    secret = _get_execution_token_secret()
    if ttl_seconds is None:
        ttl_seconds = _get_execution_token_ttl()
    now = int(time.time())
    payload = {
        "iss": EXECUTION_TOKEN_ISSUER,
        "sub": subject,
        "principal_issuer": principal_issuer,
        "exec_id": execution_id,
        "scope": EXECUTION_TOKEN_SCOPE,
        "act": {
            "iss": EXECUTION_TOKEN_ISSUER,
            "on_behalf_of": on_behalf_of,
        },
        "iat": now,
        "exp": now + ttl_seconds,
        "jti": secrets.token_hex(16),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


class ExecutionTokenProvider(AuthProvider):
    def __init__(self, registry=None):
        self._registry = registry

    async def authenticate(self, token: str) -> FluxIdentity | None:
        try:
            secret = _get_execution_token_secret()
            payload = jwt.decode(
                token,
                secret,
                algorithms=["HS256"],
                issuer=EXECUTION_TOKEN_ISSUER,
                options={"require": ["exp", "iss", "sub", "scope", "exec_id", "principal_issuer"]},
            )
            if payload.get("scope") != EXECUTION_TOKEN_SCOPE:
                logger.debug("Execution token has wrong scope — not an execution token")
                return None

            subject = payload["sub"]
            principal_issuer = payload["principal_issuer"]
            exec_id = payload["exec_id"]

            if self._registry is not None:
                principal = self._registry.find(subject, principal_issuer)
                if principal is None:
                    logger.warning(
                        f"Execution token references unknown principal ({subject}, {principal_issuer})",
                    )
                    return None
                if not principal.enabled:
                    logger.warning(f"Execution token principal '{subject}' is disabled")
                    return None
                roles = frozenset(self._registry.get_roles(principal.id))
                principal_id = principal.id
            else:
                roles = frozenset()
                principal_id = None

            return FluxIdentity(
                subject=subject,
                roles=roles,
                metadata={
                    "token_type": "execution",
                    "issuer": EXECUTION_TOKEN_ISSUER,
                    "principal_issuer": principal_issuer,
                    "exec_id": exec_id,
                    "principal_id": principal_id,
                    "jti": payload.get("jti"),
                },
            )
        except jwt.ExpiredSignatureError:
            logger.debug("Execution token expired")
            return None
        except jwt.InvalidIssuerError:
            logger.debug("Token is not an execution token (wrong issuer)")
            return None
        except jwt.InvalidTokenError as e:
            logger.debug(f"Execution token validation failed: {e}")
            return None
        except Exception as e:
            logger.error(f"ExecutionTokenProvider error: {type(e).__name__}: {e}")
            return None
