from __future__ import annotations

import os
import secrets
import stat
import time
from pathlib import Path

import jwt

from flux.config import Configuration
from flux.security.identity import FluxIdentity
from flux.security.providers import AuthProvider
from flux.utils import get_logger

logger = get_logger(__name__)

INTERNAL_ISSUER = "flux-internal"


def _get_or_create_secret() -> str:
    """Get or create the HMAC secret for internal tokens.

    The secret is auto-generated on first access and stored with 0600 permissions.
    """
    secret_file = Path(Configuration.get().settings.home) / "internal_secret"
    if secret_file.exists():
        try:
            return secret_file.read_text().strip()
        except OSError as e:
            logger.error(f"Failed to read internal secret: {e}")
            raise

    secret_file.parent.mkdir(parents=True, exist_ok=True)
    secret = secrets.token_hex(32)
    secret_file.write_text(secret)
    os.chmod(secret_file, stat.S_IRUSR | stat.S_IWUSR)
    logger.info("Generated new HMAC secret for internal tokens")
    return secret


def mint_internal_token(
    subject: str,
    roles: list[str] | frozenset[str],
    ttl_seconds: int = 1800,
) -> str:
    """Mint a short-lived HMAC-signed JWT for internal use.

    Used by the scheduler to give scheduled workflows a verifiable identity
    that workers can present back to the server for authorization checks.
    """
    secret = _get_or_create_secret()
    now = int(time.time())
    payload = {
        "iss": INTERNAL_ISSUER,
        "sub": subject,
        "roles": sorted(roles),
        "iat": now,
        "exp": now + ttl_seconds,
        "jti": secrets.token_hex(8),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


class InternalTokenProvider(AuthProvider):
    """Validates HMAC-signed internal JWTs minted by the scheduler."""

    async def authenticate(self, token: str) -> FluxIdentity | None:
        try:
            secret = _get_or_create_secret()
            payload = jwt.decode(
                token,
                secret,
                algorithms=["HS256"],
                issuer=INTERNAL_ISSUER,
                options={"require": ["exp", "iss", "sub"]},
            )
            return FluxIdentity(
                subject=payload["sub"],
                roles=frozenset(payload.get("roles", [])),
                metadata={"token_type": "internal", "jti": payload.get("jti")},
            )
        except jwt.InvalidTokenError as e:
            logger.debug(f"Internal token validation failed (may be different type): {e}")
            return None
        except Exception as e:
            logger.error(f"InternalTokenProvider error: {type(e).__name__}: {e}")
            return None
