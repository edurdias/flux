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
            {
                "iss": "flux-server",
                "sub": "alice",
                "scope": "execution",
                "exp": int(time.time()) + 600,
            },
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

    @pytest.mark.asyncio
    async def test_provider_without_registry_returns_identity_with_empty_roles(self):
        from flux.security.execution_token import ExecutionTokenProvider

        provider = ExecutionTokenProvider(registry=None)
        token = self._mint()
        identity = await provider.authenticate(token)

        assert identity is not None
        assert identity.subject == "alice@acme.com"
        assert identity.roles == frozenset()
        assert identity.metadata["principal_id"] is None
        assert identity.metadata["token_type"] == "execution"


class TestExecutionTokenTTL:
    def test_uses_config_ttl_when_none_passed(self, monkeypatch):
        monkeypatch.setenv("FLUX_EXECUTION_TOKEN_SECRET", "test-secret")
        from flux.config import Configuration
        from flux.security.execution_token import mint_execution_token

        Configuration.get().override(security={"execution_token_ttl": 3600})
        try:
            before = int(time.time())
            token = mint_execution_token(
                subject="alice",
                principal_issuer="flux",
                execution_id="exec-1",
                on_behalf_of="alice",
            )
            payload = jwt.decode(token, "test-secret", algorithms=["HS256"])
            expires_in = payload["exp"] - before
            assert 3590 <= expires_in <= 3610
        finally:
            Configuration.get().reset()

    def test_explicit_ttl_overrides_config(self, monkeypatch):
        monkeypatch.setenv("FLUX_EXECUTION_TOKEN_SECRET", "test-secret")
        from flux.config import Configuration
        from flux.security.execution_token import mint_execution_token

        Configuration.get().override(security={"execution_token_ttl": 3600})
        try:
            before = int(time.time())
            token = mint_execution_token(
                subject="alice",
                principal_issuer="flux",
                execution_id="exec-1",
                on_behalf_of="alice",
                ttl_seconds=60,
            )
            payload = jwt.decode(token, "test-secret", algorithms=["HS256"])
            expires_in = payload["exp"] - before
            assert 55 <= expires_in <= 65
        finally:
            Configuration.get().reset()

    def test_falls_back_to_default_when_config_unset(self, monkeypatch):
        monkeypatch.setenv("FLUX_EXECUTION_TOKEN_SECRET", "test-secret")
        from flux.security.execution_token import (
            _DEFAULT_EXECUTION_TOKEN_TTL,
            _get_execution_token_ttl,
        )

        ttl = _get_execution_token_ttl()
        assert ttl == _DEFAULT_EXECUTION_TOKEN_TTL


class TestExecutionTokenSecret:
    def test_env_var_takes_precedence(self, monkeypatch):
        monkeypatch.setenv("FLUX_EXECUTION_TOKEN_SECRET", "from-env")
        from flux.config import Configuration
        from flux.security.execution_token import _get_execution_token_secret

        Configuration.get().override(security={"execution_token_secret": "from-config"})
        try:
            assert _get_execution_token_secret() == "from-env"
        finally:
            Configuration.get().reset()

    def test_reads_from_config_when_env_unset(self, monkeypatch):
        monkeypatch.delenv("FLUX_EXECUTION_TOKEN_SECRET", raising=False)
        from flux.config import Configuration
        from flux.security.execution_token import _get_execution_token_secret

        Configuration.get().override(security={"execution_token_secret": "from-config"})
        try:
            assert _get_execution_token_secret() == "from-config"
        finally:
            Configuration.get().reset()

    def test_ephemeral_secret_stable_across_calls(self, monkeypatch):
        monkeypatch.delenv("FLUX_EXECUTION_TOKEN_SECRET", raising=False)
        from flux.config import Configuration
        from flux.security import execution_token as et

        et._ephemeral_secret = None
        Configuration.get().override(security={"execution_token_secret": None}, debug=True)
        try:
            first = et._get_execution_token_secret()
            second = et._get_execution_token_secret()
            assert first == second
            assert len(first) == 64
        finally:
            Configuration.get().reset()
            et._ephemeral_secret = None

    def test_ephemeral_secret_enables_mint_verify_roundtrip(self, monkeypatch):
        monkeypatch.delenv("FLUX_EXECUTION_TOKEN_SECRET", raising=False)
        from flux.config import Configuration
        from flux.security import execution_token as et
        from flux.security.execution_token import (
            ExecutionTokenProvider,
            mint_execution_token,
        )

        et._ephemeral_secret = None
        Configuration.get().override(security={"execution_token_secret": None}, debug=True)
        try:
            token = mint_execution_token(
                subject="alice",
                principal_issuer="flux",
                execution_id="exec-1",
                on_behalf_of="alice",
                ttl_seconds=600,
            )
            import asyncio

            provider = ExecutionTokenProvider(registry=None)
            identity = asyncio.run(provider.authenticate(token))
            assert identity is not None
            assert identity.subject == "alice"
        finally:
            Configuration.get().reset()
            et._ephemeral_secret = None

    def test_raises_when_nothing_configured(self, monkeypatch):
        monkeypatch.delenv("FLUX_EXECUTION_TOKEN_SECRET", raising=False)
        from flux.config import Configuration
        from flux.security import execution_token as et

        et._ephemeral_secret = None
        Configuration.get().override(security={"execution_token_secret": None}, debug=False)
        try:
            with pytest.raises(RuntimeError, match="execution_token_secret is not configured"):
                et._get_execution_token_secret()
        finally:
            Configuration.get().reset()


class TestTerminalExecutionRejection:
    """A token outlives the work it was minted for: the TTL default is 24h and
    nothing invalidated it on completion. Terminal means the token stops."""

    SECRET = "test-secret-for-unit-tests-only"

    @pytest.fixture(autouse=True)
    def patch_secret(self, monkeypatch):
        monkeypatch.setenv("FLUX_EXECUTION_TOKEN_SECRET", self.SECRET)

    @pytest.fixture
    def db(self, tmp_path, monkeypatch):
        from flux.config import Configuration
        from flux.models import DatabaseRepository
        from flux.security.execution_token import _EXECUTION_STATE_CACHE

        monkeypatch.setenv("FLUX_DATABASE_URL", f"sqlite:///{tmp_path / 'tok.db'}")
        Configuration._instance = None  # type: ignore[attr-defined]
        Configuration._config = None  # type: ignore[attr-defined]
        DatabaseRepository._engines.clear()
        Configuration.get().override(database_url=f"sqlite:///{tmp_path / 'tok.db'}")
        _EXECUTION_STATE_CACHE.clear()
        yield
        Configuration._instance = None  # type: ignore[attr-defined]
        Configuration._config = None  # type: ignore[attr-defined]
        DatabaseRepository._engines.clear()
        _EXECUTION_STATE_CACHE.clear()

    @pytest.fixture
    def provider(self):
        from unittest.mock import MagicMock

        from flux.security.execution_token import ExecutionTokenProvider
        from flux.security.principals import PrincipalModel, PrincipalRegistry

        registry = MagicMock(spec=PrincipalRegistry)
        principal = MagicMock(spec=PrincipalModel)
        principal.id = "p1"
        principal.enabled = True
        registry.find.return_value = principal
        registry.get_roles.return_value = ["operator"]
        return ExecutionTokenProvider(registry=registry)

    def _seed(self, execution_id: str, state) -> None:
        from flux import ExecutionContext
        from flux.context_managers import ContextManager
        from flux.models import ExecutionContextModel
        from flux.unit_of_work import UnitOfWork

        ctx: ExecutionContext = ExecutionContext(
            workflow_id="default/release",
            workflow_namespace="default",
            workflow_name="release",
            input=None,
            execution_id=execution_id,
        )
        ContextManager.create().save(ctx)
        with UnitOfWork() as uow:
            uow.session.get(ExecutionContextModel, execution_id).state = state
            uow.commit()

    def _token(self, execution_id: str) -> str:
        return mint_execution_token(
            subject="alice@acme.com",
            principal_issuer="flux",
            execution_id=execution_id,
            on_behalf_of="alice@acme.com",
            ttl_seconds=600,
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("state", ["COMPLETED", "FAILED", "CANCELLED"])
    async def test_terminal_execution_rejects_the_token(self, provider, db, state):
        from flux.domain import ExecutionState

        eid = f"exec-term-{state.lower()}"
        self._seed(eid, ExecutionState(state))
        assert await provider.authenticate(self._token(eid)) is None

    @pytest.mark.asyncio
    async def test_running_execution_still_authenticates(self, provider, db):
        from flux.domain import ExecutionState

        eid = "exec-running"
        self._seed(eid, ExecutionState.RUNNING)
        identity = await provider.authenticate(self._token(eid))
        assert identity is not None
        assert identity.metadata["exec_id"] == eid

    @pytest.mark.asyncio
    async def test_unknown_execution_is_not_rejected(self, provider, db):
        """Deliberate: only a row that exists and is terminal stops the token.
        Failing closed on a missing or unreadable row would turn a datastore
        problem into an authentication outage for every running execution."""
        assert await provider.authenticate(self._token("exec-never-existed")) is not None
