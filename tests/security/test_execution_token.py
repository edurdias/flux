from __future__ import annotations

import os
import time
from unittest.mock import MagicMock

import jwt
import pytest

from flux.security.execution_token import mint_execution_token


class TestMintExecutionToken:
    @pytest.fixture(autouse=True)
    def patch_secret(self, monkeypatch):
        monkeypatch.setenv("FLUX_EXECUTION_TOKEN_SECRET", "test-secret-for-unit-tests-only")

    def test_mint_returns_string(self):
        token = mint_execution_token(
            subject="alice@acme.com",
            principal_issuer="https://auth.example.com",
            execution_id="exec-001",
            on_behalf_of="alice@acme.com",
            ttl_seconds=600,
        )
        assert isinstance(token, str)
        assert len(token) > 0

    def test_mint_roundtrip_claims(self):
        token = mint_execution_token(
            subject="alice@acme.com",
            principal_issuer="https://auth.example.com",
            execution_id="exec-001",
            on_behalf_of="alice@acme.com",
            ttl_seconds=600,
        )
        payload = jwt.decode(token, "test-secret-for-unit-tests-only", algorithms=["HS256"])
        assert payload["iss"] == "flux-server"
        assert payload["sub"] == "alice@acme.com"
        assert payload["principal_issuer"] == "https://auth.example.com"
        assert payload["exec_id"] == "exec-001"
        assert payload["scope"] == "execution"
        assert payload["act"]["on_behalf_of"] == "alice@acme.com"
        assert "jti" in payload
        assert payload["exp"] > int(time.time())

    def test_mint_different_tokens_have_unique_jti(self):
        t1 = mint_execution_token(
            subject="alice@acme.com",
            principal_issuer="flux",
            execution_id="exec-001",
            on_behalf_of="alice@acme.com",
            ttl_seconds=600,
        )
        t2 = mint_execution_token(
            subject="alice@acme.com",
            principal_issuer="flux",
            execution_id="exec-001",
            on_behalf_of="alice@acme.com",
            ttl_seconds=600,
        )
        p1 = jwt.decode(t1, "test-secret-for-unit-tests-only", algorithms=["HS256"])
        p2 = jwt.decode(t2, "test-secret-for-unit-tests-only", algorithms=["HS256"])
        assert p1["jti"] != p2["jti"]


class TestExecutionTokenProvider:
    SECRET = "test-secret-for-unit-tests-only"

    @pytest.fixture(autouse=True)
    def patch_secret(self, monkeypatch):
        monkeypatch.setenv("FLUX_EXECUTION_TOKEN_SECRET", self.SECRET)

    @pytest.fixture
    def mock_registry(self):
        from flux.security.principals import PrincipalRegistry

        return MagicMock(spec=PrincipalRegistry)

    @pytest.fixture
    def provider(self, mock_registry):
        from flux.security.execution_token import ExecutionTokenProvider

        return ExecutionTokenProvider(registry=mock_registry)

    def _mint(
        self,
        subject="alice@acme.com",
        principal_issuer="flux",
        exec_id="exec-001",
        on_behalf_of="alice@acme.com",
        ttl=600,
    ):
        return mint_execution_token(
            subject=subject,
            principal_issuer=principal_issuer,
            execution_id=exec_id,
            on_behalf_of=on_behalf_of,
            ttl_seconds=ttl,
        )

    @pytest.mark.asyncio
    async def test_validate_valid_token(self, provider, mock_registry):
        from flux.security.principals import PrincipalModel

        token = self._mint()
        mock_principal = MagicMock(spec=PrincipalModel)
        mock_principal.id = "p1"
        mock_principal.enabled = True
        mock_registry.find.return_value = mock_principal
        mock_registry.get_roles.return_value = ["operator"]

        identity = await provider.authenticate(token)

        assert identity is not None
        assert identity.subject == "alice@acme.com"
        assert identity.metadata["token_type"] == "execution"
        assert identity.metadata["exec_id"] == "exec-001"

    @pytest.mark.asyncio
    async def test_validate_expired_token(self, provider):
        token = self._mint(ttl=-1)
        identity = await provider.authenticate(token)
        assert identity is None

    @pytest.mark.asyncio
    async def test_validate_wrong_signature(self, provider):
        bad_token = jwt.encode(
            {"iss": "flux-server", "sub": "alice", "scope": "execution", "exp": int(time.time()) + 600},
            "wrong-secret",
            algorithm="HS256",
        )
        identity = await provider.authenticate(bad_token)
        assert identity is None

    @pytest.mark.asyncio
    async def test_validate_wrong_scope(self, provider):
        secret = os.environ.get("FLUX_EXECUTION_TOKEN_SECRET", "test-secret-for-unit-tests-only")
        token = jwt.encode(
            {
                "iss": "flux-server",
                "sub": "alice",
                "scope": "admin",
                "principal_issuer": "flux",
                "exec_id": "exec-001",
                "exp": int(time.time()) + 600,
                "iat": int(time.time()),
                "jti": "abc",
            },
            secret,
            algorithm="HS256",
        )
        identity = await provider.authenticate(token)
        assert identity is None

    @pytest.mark.asyncio
    async def test_validate_wrong_issuer(self, provider):
        secret = os.environ.get("FLUX_EXECUTION_TOKEN_SECRET", "test-secret-for-unit-tests-only")
        token = jwt.encode(
            {
                "iss": "not-flux-server",
                "sub": "alice",
                "scope": "execution",
                "principal_issuer": "flux",
                "exec_id": "exec-001",
                "exp": int(time.time()) + 600,
                "iat": int(time.time()),
                "jti": "abc",
            },
            secret,
            algorithm="HS256",
        )
        identity = await provider.authenticate(token)
        assert identity is None

    @pytest.mark.asyncio
    async def test_exec_id_preserved_in_metadata(self, provider, mock_registry):
        from flux.security.principals import PrincipalModel

        token = self._mint(exec_id="exec-xyz-789")
        mock_principal = MagicMock(spec=PrincipalModel)
        mock_principal.id = "p1"
        mock_principal.enabled = True
        mock_registry.find.return_value = mock_principal
        mock_registry.get_roles.return_value = []

        identity = await provider.authenticate(token)
        assert identity.metadata["exec_id"] == "exec-xyz-789"

    @pytest.mark.asyncio
    async def test_disabled_principal_returns_none(self, provider, mock_registry):
        from flux.security.principals import PrincipalModel

        token = self._mint()
        mock_principal = MagicMock(spec=PrincipalModel)
        mock_principal.id = "p1"
        mock_principal.enabled = False
        mock_registry.find.return_value = mock_principal

        identity = await provider.authenticate(token)
        assert identity is None

    @pytest.mark.asyncio
    async def test_unknown_principal_returns_none(self, provider, mock_registry):
        token = self._mint()
        mock_registry.find.return_value = None
        identity = await provider.authenticate(token)
        assert identity is None
