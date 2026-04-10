from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from flux.config import EncryptionConfig


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


class AuthConfig(_BaseConfig):
    oidc: OIDCConfig = Field(default_factory=OIDCConfig)
    api_keys: APIKeyAuthConfig = Field(default_factory=APIKeyAuthConfig)
    default_user_roles: list[str] = Field(default_factory=list)

    @property
    def enabled(self) -> bool:
        return self.oidc.enabled or self.api_keys.enabled


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
        default=604800,
        description="Execution token TTL in seconds (default: 7 days).",
    )

    model_config = {"arbitrary_types_allowed": True}
