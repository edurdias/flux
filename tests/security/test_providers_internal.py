from __future__ import annotations

from pathlib import Path

import pytest

from flux.security.providers.internal import (
    InternalTokenProvider,
    mint_internal_token,
    _get_or_create_secret,
)


class TestInternalTokenProvider:
    @pytest.fixture
    def provider(self):
        return InternalTokenProvider()

    @pytest.fixture(autouse=True)
    def _setup_secret(self, tmp_path, monkeypatch):
        from flux.config import Configuration

        settings = Configuration.get().settings
        original_home = settings.home
        settings.home = str(tmp_path)
        yield
        settings.home = original_home

    @pytest.mark.asyncio
    async def test_mint_and_validate_roundtrip(self, provider):
        token = mint_internal_token(subject="sa:test", roles=["admin"])
        identity = await provider.authenticate(token)
        assert identity is not None
        assert identity.subject == "sa:test"
        assert "admin" in identity.roles
        assert identity.metadata.get("token_type") == "internal"

    @pytest.mark.asyncio
    async def test_invalid_token_returns_none(self, provider):
        identity = await provider.authenticate("not-a-valid-jwt")
        assert identity is None

    @pytest.mark.asyncio
    async def test_expired_token_returns_none(self, provider):
        token = mint_internal_token(subject="sa:test", roles=["admin"], ttl_seconds=-1)
        identity = await provider.authenticate(token)
        assert identity is None

    @pytest.mark.asyncio
    async def test_wrong_secret_returns_none(self, provider, tmp_path):
        token = mint_internal_token(subject="sa:test", roles=["admin"])
        from flux.config import Configuration

        secret_file = Path(Configuration.get().settings.home) / "internal_secret"
        secret_file.write_text("different-secret-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")
        identity = await provider.authenticate(token)
        assert identity is None

    @pytest.mark.asyncio
    async def test_multiple_roles_preserved(self, provider):
        token = mint_internal_token(subject="sa:ci", roles=["operator", "viewer"])
        identity = await provider.authenticate(token)
        assert identity is not None
        assert "operator" in identity.roles
        assert "viewer" in identity.roles

    @pytest.mark.asyncio
    async def test_jti_in_metadata(self, provider):
        token = mint_internal_token(subject="sa:test", roles=["admin"])
        identity = await provider.authenticate(token)
        assert identity is not None
        assert identity.metadata.get("jti") is not None

    def test_secret_file_created_with_restricted_permissions(self, tmp_path):
        from flux.config import Configuration

        settings = Configuration.get().settings
        original_home = settings.home
        settings.home = str(tmp_path)
        try:
            secret = _get_or_create_secret()
            secret_file = tmp_path / "internal_secret"
            assert secret_file.exists()
            assert len(secret) == 64
            mode = oct(secret_file.stat().st_mode)[-3:]
            assert mode == "600"
        finally:
            settings.home = original_home

    def test_secret_reused_on_second_call(self, tmp_path):
        from flux.config import Configuration

        settings = Configuration.get().settings
        original_home = settings.home
        settings.home = str(tmp_path)
        try:
            secret1 = _get_or_create_secret()
            secret2 = _get_or_create_secret()
            assert secret1 == secret2
        finally:
            settings.home = original_home
