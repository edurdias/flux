from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from flux.security.config import OIDCConfig
from flux.security.errors import AuthenticationError
from flux.security.principals import PrincipalRegistry
from flux.security.providers.oidc import OIDCProvider


def _generate_rsa_key_pair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


class TestOIDCProviderValidation:
    @pytest.fixture
    def rsa_keys(self):
        return _generate_rsa_key_pair()

    @pytest.fixture
    def config(self):
        return OIDCConfig(
            enabled=True,
            issuer="https://auth.example.com",
            audience="flux-api",
        )

    @pytest.fixture
    def mock_registry(self):
        return MagicMock(spec=PrincipalRegistry)

    @pytest.fixture
    def provider(self, config, mock_registry):
        return OIDCProvider(config, registry=mock_registry)

    def _make_token(self, rsa_keys, claims: dict) -> str:
        private_key, _ = rsa_keys
        default_claims = {
            "iss": "https://auth.example.com",
            "aud": "flux-api",
            "sub": "alice@acme.com",
            "exp": int(time.time()) + 3600,
            "iat": int(time.time()),
            "name": "Alice Smith",
        }
        default_claims.update(claims)
        return pyjwt.encode(default_claims, private_key, algorithm="RS256")

    @pytest.mark.asyncio
    async def test_authenticate_valid_token_returns_principal_identity(
        self,
        provider,
        rsa_keys,
        mock_registry,
    ):
        _, public_key = rsa_keys
        token = self._make_token(rsa_keys, {})

        mock_principal = MagicMock()
        mock_principal.id = "p1"
        mock_principal.enabled = True
        mock_principal.display_name = "Alice"
        mock_registry.find.return_value = mock_principal
        mock_registry.get_roles.return_value = ["operator"]

        with patch.object(provider, "_get_signing_key", return_value=public_key):
            identity = await provider.authenticate(token)

        assert identity is not None
        assert identity.subject == "alice@acme.com"
        assert "operator" in identity.roles
        assert identity.metadata["principal_id"] == "p1"

    @pytest.mark.asyncio
    async def test_authenticate_ignores_jwt_roles_claim(
        self,
        provider,
        rsa_keys,
        mock_registry,
    ):
        _, public_key = rsa_keys
        token = self._make_token(rsa_keys, {"roles": ["admin"]})

        mock_principal = MagicMock()
        mock_principal.id = "p1"
        mock_principal.enabled = True
        mock_principal.display_name = "Alice"
        mock_registry.find.return_value = mock_principal
        mock_registry.get_roles.return_value = ["viewer"]

        with patch.object(provider, "_get_signing_key", return_value=public_key):
            identity = await provider.authenticate(token)

        assert identity is not None
        assert "admin" not in identity.roles
        assert "viewer" in identity.roles

    @pytest.mark.asyncio
    async def test_authenticate_expired_token_returns_none(self, provider, rsa_keys):
        _, public_key = rsa_keys
        token = self._make_token(rsa_keys, {"exp": int(time.time()) - 100})

        with patch.object(provider, "_get_signing_key", return_value=public_key):
            identity = await provider.authenticate(token)

        assert identity is None

    @pytest.mark.asyncio
    async def test_authenticate_wrong_audience_returns_none(self, provider, rsa_keys):
        _, public_key = rsa_keys
        token = self._make_token(rsa_keys, {"aud": "wrong-api"})

        with patch.object(provider, "_get_signing_key", return_value=public_key):
            identity = await provider.authenticate(token)

        assert identity is None

    @pytest.mark.asyncio
    async def test_authenticate_disabled_principal_raises(
        self,
        provider,
        rsa_keys,
        mock_registry,
    ):
        _, public_key = rsa_keys
        token = self._make_token(rsa_keys, {})

        mock_principal = MagicMock()
        mock_principal.id = "p1"
        mock_principal.enabled = False
        mock_registry.find.return_value = mock_principal

        with pytest.raises(AuthenticationError, match="Principal disabled"):
            with patch.object(provider, "_get_signing_key", return_value=public_key):
                await provider.authenticate(token)

    @pytest.mark.asyncio
    async def test_authenticate_updates_last_seen(
        self,
        provider,
        rsa_keys,
        mock_registry,
    ):
        _, public_key = rsa_keys
        token = self._make_token(rsa_keys, {})

        mock_principal = MagicMock()
        mock_principal.id = "p1"
        mock_principal.enabled = True
        mock_principal.display_name = "Alice"
        mock_registry.find.return_value = mock_principal
        mock_registry.get_roles.return_value = []

        with patch.object(provider, "_get_signing_key", return_value=public_key):
            await provider.authenticate(token)

        mock_registry.update_last_seen.assert_called_once_with("p1")

    @pytest.mark.asyncio
    async def test_authenticate_does_not_store_email(
        self,
        provider,
        rsa_keys,
        mock_registry,
    ):
        _, public_key = rsa_keys
        token = self._make_token(
            rsa_keys,
            {"email": "alice@acme.com", "email_verified": True},
        )

        mock_principal = MagicMock()
        mock_principal.id = "p1"
        mock_principal.enabled = True
        mock_principal.display_name = "Alice"
        mock_registry.find.return_value = mock_principal
        mock_registry.get_roles.return_value = []

        with patch.object(provider, "_get_signing_key", return_value=public_key):
            await provider.authenticate(token)

        call = mock_registry.update_metadata.call_args
        metadata = call.kwargs.get("metadata") or call.args[1] if len(call.args) > 1 else {}
        assert "email" not in metadata
        assert "email_verified" not in metadata


class TestOIDCProviderInitValidation:
    """OIDC must reject empty issuer/audience when enabled (token-confusion guard)."""

    def test_disabled_oidc_with_empty_strings_is_allowed(self):
        OIDCProvider(OIDCConfig(enabled=False, issuer="", audience=""))

    def test_enabled_oidc_with_empty_issuer_raises(self):
        with pytest.raises(ValueError, match="issuer is empty"):
            OIDCProvider(OIDCConfig(enabled=True, issuer="", audience="flux-api"))

    def test_enabled_oidc_with_empty_audience_raises(self):
        with pytest.raises(ValueError, match="audience is empty"):
            OIDCProvider(OIDCConfig(enabled=True, issuer="https://idp", audience=""))

    def test_enabled_oidc_with_full_config_is_accepted(self):
        OIDCProvider(
            OIDCConfig(enabled=True, issuer="https://idp", audience="flux-api"),
        )
