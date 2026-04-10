from __future__ import annotations

import time
from unittest.mock import patch

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from flux.models import Base
from flux.security.config import OIDCConfig
from flux.security.errors import AuthenticationError
from flux.security.principals import PrincipalRegistry
from flux.security.models import RoleModel
from flux.security.providers.oidc import OIDCProvider


def _generate_rsa_key_pair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


@pytest.fixture
def engine():
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture
def session(engine):
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()


@pytest.fixture
def registry(session):
    return PrincipalRegistry(session_factory=lambda: session)


@pytest.fixture
def rsa_keys():
    return _generate_rsa_key_pair()


def _make_token(rsa_keys, claims: dict) -> str:
    private_key, _ = rsa_keys
    default_claims = {
        "iss": "https://auth.example.com",
        "aud": "flux-api",
        "sub": "alice@acme.com",
        "exp": int(time.time()) + 3600,
        "iat": int(time.time()),
        "name": "Alice Smith",
        "given_name": "Alice",
        "family_name": "Smith",
    }
    default_claims.update(claims)
    return pyjwt.encode(default_claims, private_key, algorithm="RS256")


class TestAutoProvisioningJITMode:
    @pytest.fixture
    def config(self):
        return OIDCConfig(
            enabled=True,
            issuer="https://auth.example.com",
            audience="flux-api",
            default_user_roles=["viewer"],
        )

    @pytest.fixture
    def provider(self, config, registry, session):
        session.add(RoleModel(name="viewer", permissions=["workflow:*:read"], built_in=True))
        session.commit()
        return OIDCProvider(config, registry=registry)

    @pytest.mark.asyncio
    async def test_first_login_provisions_principal(self, provider, registry, rsa_keys):
        _, public_key = rsa_keys
        token = _make_token(rsa_keys, {})

        with patch.object(provider, "_get_signing_key", return_value=public_key):
            identity = await provider.authenticate(token)

        assert identity is not None
        assert identity.subject == "alice@acme.com"
        assert "viewer" in identity.roles

        principal = registry.find("alice@acme.com", "https://auth.example.com")
        assert principal is not None
        assert principal.enabled is True

    @pytest.mark.asyncio
    async def test_second_login_updates_last_seen(self, provider, registry, rsa_keys):
        _, public_key = rsa_keys
        token = _make_token(rsa_keys, {})

        with patch.object(provider, "_get_signing_key", return_value=public_key):
            await provider.authenticate(token)
            await provider.authenticate(token)

        principal = registry.find("alice@acme.com", "https://auth.example.com")
        assert principal.last_seen_at is not None

    @pytest.mark.asyncio
    async def test_disabled_principal_raises(self, provider, registry, rsa_keys):
        _, public_key = rsa_keys
        token = _make_token(rsa_keys, {})

        with patch.object(provider, "_get_signing_key", return_value=public_key):
            await provider.authenticate(token)

        principal = registry.find("alice@acme.com", "https://auth.example.com")
        registry.set_enabled(principal.id, False)

        with pytest.raises(AuthenticationError, match="Principal disabled"):
            with patch.object(provider, "_get_signing_key", return_value=public_key):
                await provider.authenticate(token)


class TestAutoProvisioningStrictMode:
    @pytest.fixture
    def config(self):
        return OIDCConfig(
            enabled=True,
            issuer="https://auth.example.com",
            audience="flux-api",
            default_user_roles=[],
        )

    @pytest.fixture
    def provider(self, config, registry):
        return OIDCProvider(config, registry=registry)

    @pytest.mark.asyncio
    async def test_unprovioned_user_raises(self, provider, rsa_keys):
        _, public_key = rsa_keys
        token = _make_token(rsa_keys, {})

        with pytest.raises(AuthenticationError, match="Principal not provisioned"):
            with patch.object(provider, "_get_signing_key", return_value=public_key):
                await provider.authenticate(token)

    @pytest.mark.asyncio
    async def test_pre_provisioned_user_authenticates(self, provider, registry, rsa_keys, session):
        session.add(RoleModel(name="operator", permissions=["workflow:*:run"], built_in=False))
        session.commit()

        principal = registry.create(
            type="user",
            subject="alice@acme.com",
            external_issuer="https://auth.example.com",
            display_name="Alice",
            metadata={},
        )
        registry.assign_role(principal.id, "operator", assigned_by="admin")

        _, public_key = rsa_keys
        token = _make_token(rsa_keys, {})

        with patch.object(provider, "_get_signing_key", return_value=public_key):
            identity = await provider.authenticate(token)

        assert identity is not None
        assert "operator" in identity.roles
