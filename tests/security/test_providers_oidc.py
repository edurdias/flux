from __future__ import annotations

import time
from unittest.mock import patch

import httpx
import jwt as pyjwt
from cryptography.hazmat.primitives.asymmetric import rsa

import pytest

from flux.security.providers.oidc import OIDCProvider
from flux.security.config import OIDCConfig


def _generate_rsa_key_pair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()
    return private_key, public_key


class TestOIDCProvider:
    @pytest.fixture
    def rsa_keys(self):
        return _generate_rsa_key_pair()

    @pytest.fixture
    def config(self):
        return OIDCConfig(
            enabled=True,
            issuer="https://auth.example.com",
            audience="flux-api",
            roles_claim="roles",
        )

    @pytest.fixture
    def provider(self, config):
        return OIDCProvider(config)

    def _make_token(self, rsa_keys, claims: dict) -> str:
        private_key, _ = rsa_keys
        default_claims = {
            "iss": "https://auth.example.com",
            "aud": "flux-api",
            "sub": "alice@acme.com",
            "exp": int(time.time()) + 3600,
            "iat": int(time.time()),
            "roles": ["operator"],
        }
        default_claims.update(claims)
        return pyjwt.encode(default_claims, private_key, algorithm="RS256")

    @pytest.mark.asyncio
    async def test_authenticate_valid_token(self, provider, rsa_keys):
        _, public_key = rsa_keys
        token = self._make_token(rsa_keys, {})

        with patch.object(provider, "_get_signing_key") as mock_key:
            mock_key.return_value = public_key
            identity = await provider.authenticate(token)

        assert identity is not None
        assert identity.subject == "alice@acme.com"
        assert "operator" in identity.roles

    @pytest.mark.asyncio
    async def test_authenticate_expired_token(self, provider, rsa_keys):
        _, public_key = rsa_keys
        token = self._make_token(rsa_keys, {"exp": int(time.time()) - 100})

        with patch.object(provider, "_get_signing_key") as mock_key:
            mock_key.return_value = public_key
            identity = await provider.authenticate(token)

        assert identity is None

    @pytest.mark.asyncio
    async def test_authenticate_wrong_audience(self, provider, rsa_keys):
        _, public_key = rsa_keys
        token = self._make_token(rsa_keys, {"aud": "wrong-api"})

        with patch.object(provider, "_get_signing_key") as mock_key:
            mock_key.return_value = public_key
            identity = await provider.authenticate(token)

        assert identity is None

    @pytest.mark.asyncio
    async def test_nested_roles_claim(self, rsa_keys):
        config = OIDCConfig(
            enabled=True,
            issuer="https://auth.example.com",
            audience="flux-api",
            roles_claim="realm_access.roles",
        )
        provider = OIDCProvider(config)
        _, public_key = rsa_keys
        token = self._make_token(
            rsa_keys,
            {
                "roles": [],
                "realm_access": {"roles": ["admin"]},
            },
        )

        with patch.object(provider, "_get_signing_key") as mock_key:
            mock_key.return_value = public_key
            identity = await provider.authenticate(token)

        assert identity is not None
        assert "admin" in identity.roles

    def test_resolve_claim_flat(self):
        assert OIDCProvider._resolve_claim({"roles": ["admin"]}, "roles") == ["admin"]

    def test_resolve_claim_nested(self):
        payload = {"realm_access": {"roles": ["operator"]}}
        assert OIDCProvider._resolve_claim(payload, "realm_access.roles") == ["operator"]

    def test_resolve_claim_missing(self):
        assert OIDCProvider._resolve_claim({"other": "value"}, "roles") is None

    @pytest.mark.asyncio
    async def test_authenticate_returns_none_on_discovery_failure(self, provider):
        with patch.object(
            provider,
            "_get_signing_key",
            side_effect=Exception("Network error fetching JWKS"),
        ):
            identity = await provider.authenticate("some.token.value")

        assert identity is None

    @pytest.mark.asyncio
    async def test_authenticate_returns_none_on_http_error(self, provider):
        with patch.object(
            provider,
            "_get_signing_key",
            side_effect=httpx.ConnectError("Connection refused"),
        ):
            identity = await provider.authenticate("some.token.value")

        assert identity is None
