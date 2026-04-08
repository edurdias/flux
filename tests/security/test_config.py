from flux.security.config import AuthConfig, OIDCConfig, APIKeyAuthConfig, SecurityConfig
from flux.config import EncryptionConfig


class TestAuthConfig:
    def test_auth_disabled_by_default(self):
        config = AuthConfig()
        assert config.enabled is False

    def test_auth_enabled_when_oidc_enabled(self):
        config = AuthConfig(oidc=OIDCConfig(enabled=True, issuer="https://example.com", audience="flux"))
        assert config.enabled is True

    def test_auth_enabled_when_api_keys_enabled(self):
        config = AuthConfig(api_keys=APIKeyAuthConfig(enabled=True))
        assert config.enabled is True

    def test_auth_disabled_when_all_providers_disabled(self):
        config = AuthConfig(
            oidc=OIDCConfig(enabled=False),
            api_keys=APIKeyAuthConfig(enabled=False),
        )
        assert config.enabled is False

    def test_oidc_defaults(self):
        config = OIDCConfig()
        assert config.roles_claim == "roles"
        assert config.jwks_cache_ttl == 3600
        assert config.clock_skew == 30

    def test_security_config_contains_both(self):
        config = SecurityConfig()
        assert isinstance(config.encryption, EncryptionConfig)
        assert isinstance(config.auth, AuthConfig)
