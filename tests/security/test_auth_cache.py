"""Tests for the per-process auth resolution cache."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from flux.security.auth_service import AuthService
from flux.security.config import APIKeyAuthConfig, AuthConfig
from flux.security.identity import FluxIdentity
from flux.security.models import RoleModel


def make_service(ttl: float = 30.0, registry=None) -> tuple[AuthService, MagicMock]:
    config = AuthConfig(
        api_keys=APIKeyAuthConfig(enabled=True),
        resolution_cache_ttl=ttl,
    )
    session = MagicMock()
    role = MagicMock(spec=RoleModel)
    role.name = "operator"
    role.permissions = ["workflow:*:run"]
    session.query.return_value.filter_by.return_value.first.return_value = role
    factory = MagicMock(side_effect=lambda: session)
    service = AuthService(config=config, session_factory=factory, registry=registry)
    return service, factory


class TestPermissionCache:
    @pytest.mark.asyncio
    async def test_repeat_resolution_hits_cache(self):
        service, factory = make_service()
        identity = FluxIdentity(subject="w1", roles=frozenset({"operator"}))

        first = await service.resolve_permissions(identity)
        second = await service.resolve_permissions(identity)

        assert first == second == {"workflow:*:run"}
        assert factory.call_count == 1  # second call never opened a session

    @pytest.mark.asyncio
    async def test_ttl_zero_disables_cache(self):
        service, factory = make_service(ttl=0)
        identity = FluxIdentity(subject="w1", roles=frozenset({"operator"}))

        await service.resolve_permissions(identity)
        await service.resolve_permissions(identity)

        assert factory.call_count == 2

    @pytest.mark.asyncio
    async def test_distinct_identities_do_not_share_entries(self):
        service, _ = make_service()
        operator = FluxIdentity(subject="w1", roles=frozenset({"operator"}))
        viewer = FluxIdentity(subject="w1", roles=frozenset({"viewer"}))

        op_perms = await service.resolve_permissions(operator)
        viewer_perms = await service.resolve_permissions(viewer)

        # Both resolve through the same mocked role row, but via different
        # cache keys — the viewer must not receive the operator's cached set.
        assert op_perms is not viewer_perms

    @pytest.mark.asyncio
    async def test_role_mutation_invalidates_cache(self):
        registry = MagicMock()
        registry.get_roles.return_value = ["operator"]
        registry.assign_role.return_value = None
        service, factory = make_service(registry=registry)
        identity = FluxIdentity(
            subject="w1",
            roles=frozenset(),
            metadata={"principal_id": "p1"},
        )

        await service.resolve_permissions(identity)
        assert factory.call_count == 1

        await service.grant_role("p1", "admin")

        await service.resolve_permissions(identity)
        assert factory.call_count == 2  # cache was cleared, session reopened


class TestIdentityCache:
    @pytest.mark.asyncio
    async def test_repeat_authentication_hits_cache(self):
        service, _ = make_service()
        identity = FluxIdentity(subject="worker-1", roles=frozenset({"worker"}))
        provider = MagicMock()
        provider.authenticate = AsyncMock(return_value=identity)
        service._providers = [provider]

        first = await service.authenticate("token-abc")
        second = await service.authenticate("token-abc")

        assert first is identity and second is identity
        provider.authenticate.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_failed_authentication_is_not_cached(self):
        from flux.security.errors import AuthenticationError

        service, _ = make_service()
        provider = MagicMock()
        provider.authenticate = AsyncMock(return_value=None)
        service._providers = [provider]

        with pytest.raises(AuthenticationError):
            await service.authenticate("bad-token")
        with pytest.raises(AuthenticationError):
            await service.authenticate("bad-token")

        assert provider.authenticate.await_count == 2

    @pytest.mark.asyncio
    async def test_key_revocation_invalidates_identity_cache(self):
        service, _ = make_service()
        identity = FluxIdentity(subject="worker-1", roles=frozenset({"worker"}))
        provider = MagicMock()
        provider.authenticate = AsyncMock(return_value=identity)
        service._providers = [provider]

        await service.authenticate("token-abc")
        service.invalidate_resolution_caches()
        await service.authenticate("token-abc")

        assert provider.authenticate.await_count == 2
