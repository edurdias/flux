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
    roles_claim: str = "roles"
    jwks_cache_ttl: int = Field(default=3600, description="Seconds to cache JWKS keys")
    clock_skew: int = Field(default=30, description="Seconds of leeway for exp/nbf")


class APIKeyAuthConfig(_BaseConfig):
    enabled: bool = False


class AuthConfig(_BaseConfig):
    oidc: OIDCConfig = Field(default_factory=OIDCConfig)
    api_keys: APIKeyAuthConfig = Field(default_factory=APIKeyAuthConfig)

    @property
    def enabled(self) -> bool:
        return self.oidc.enabled or self.api_keys.enabled


def _make_encryption_config():
    from flux.config import EncryptionConfig
    return EncryptionConfig()


class SecurityConfig(_BaseConfig):
    encryption: EncryptionConfig = Field(default_factory=_make_encryption_config)
    auth: AuthConfig = Field(default_factory=AuthConfig)

    model_config = {"arbitrary_types_allowed": True}
