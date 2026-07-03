from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field, model_validator

if TYPE_CHECKING:
    from flux.config import EncryptionConfig

logger = logging.getLogger(__name__)


class _BaseConfig(BaseModel):
    def to_dict(self) -> dict:
        return self.model_dump()


class OIDCConfig(_BaseConfig):
    enabled: bool = False
    issuer: str = ""
    audience: str = ""
    roles_claim: str = Field(
        default="roles",
        description="Deprecated: Flux no longer reads roles from OIDC tokens. Roles are managed via the principals registry.",
    )
    jwks_cache_ttl: int = Field(default=3600, description="Seconds to cache JWKS keys")
    clock_skew: int = Field(default=30, description="Seconds of leeway for exp/nbf")
    default_user_roles: list[str] = Field(default_factory=list)


class APIKeyAuthConfig(_BaseConfig):
    enabled: bool = False
    worker_key_ttl: int = Field(
        default=604800,
        description=(
            "Lifetime in seconds of API keys minted for workers at "
            "registration (default 7 days; 0 = never expire). Workers "
            "re-register automatically on 401, so expiry rotates keys "
            "without operator action."
        ),
    )


class AuthConfig(_BaseConfig):
    oidc: OIDCConfig = Field(default_factory=OIDCConfig)
    api_keys: APIKeyAuthConfig = Field(default_factory=APIKeyAuthConfig)
    enabled: bool = Field(
        default=False,
        description=(
            "Master switch for authentication. Settable via "
            "FLUX_SECURITY__AUTH__ENABLED. Enabling any provider implies it is "
            "on; when on, at least one provider (oidc/api_keys) must be enabled."
        ),
    )
    allow_anonymous: bool = Field(
        default=False,
        description=(
            "When auth is disabled, this must be set true to permit anonymous "
            "state-changing requests (POST/PUT/PATCH/DELETE). Defaults to false "
            "so a server started without authentication will not perform "
            "privileged operations until anonymous access is explicitly accepted "
            "(FLUX_SECURITY__AUTH__ALLOW_ANONYMOUS=true) or auth is enabled. "
            "Has no effect when auth is enabled."
        ),
    )

    @model_validator(mode="after")
    def _reconcile_enabled(self) -> AuthConfig:
        provider_enabled = self.oidc.enabled or self.api_keys.enabled
        if provider_enabled:
            # Enabling a provider implies auth is on. Surface the override so a
            # deliberate enabled=false isn't silently flipped back.
            if not self.enabled:
                logger.warning(
                    "security.auth.enabled was false but an auth provider is "
                    "enabled; forcing security.auth.enabled=true.",
                )
            self.enabled = True
        elif self.enabled:
            raise ValueError(
                "security.auth.enabled is true but no auth provider is "
                "configured. Enable [flux.security.auth.oidc] or "
                "[flux.security.auth.api_keys].",
            )
        return self


def _make_encryption_config():
    from flux.config import EncryptionConfig

    return EncryptionConfig()


class SecurityConfig(_BaseConfig):
    encryption: EncryptionConfig = Field(default_factory=_make_encryption_config)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    execution_token_secret: str | None = Field(
        default=None,
        description="HMAC secret for signing execution tokens. Required in production.",
    )
    execution_token_ttl: int = Field(
        default=86400,
        description=(
            "Execution token TTL in seconds (default: 24 hours). The token is "
            "scoped to a single execution and minted fresh on every dispatch "
            "and resume, so it only needs to outlive one continuous run."
        ),
    )

    model_config = {"arbitrary_types_allowed": True}
