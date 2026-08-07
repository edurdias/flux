"""First-admin bootstrap (issue #154).

On the first auth-enabled start with no admin principal, the server seeds
a bootstrap admin: random API key, hash-only in the database bound to the
built-in admin role, plaintext delivered via a host-local 0600 file.
Init-only, idempotent against partial runs, rotatable.
"""

from __future__ import annotations

import hashlib
import stat

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from flux.models import Base
from flux.security import admin_bootstrap
from flux.security.auth_service import AuthService
from flux.security.principals import PrincipalRegistry


@pytest.fixture
def session_factory():
    import flux.security.models  # noqa: F401 — register security models on Base

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


@pytest.fixture
def registry(session_factory):
    return PrincipalRegistry(session_factory=session_factory)


@pytest.fixture
def auth_service(session_factory, registry):
    from flux.config import Configuration

    service = AuthService(
        config=Configuration.get().settings.security.auth,
        session_factory=session_factory,
        registry=registry,
    )
    service.seed_built_in_roles()
    return service


@pytest.fixture
def enabled_auth_service(session_factory, registry):
    """An ``AuthService`` wired the way the server wires it with the API-key
    provider on — the configuration the bootstrap key is minted for."""
    from flux.security.config import APIKeyAuthConfig, AuthConfig

    service = AuthService(
        config=AuthConfig(enabled=True, api_keys=APIKeyAuthConfig(enabled=True)),
        session_factory=session_factory,
        registry=registry,
    )
    service.seed_built_in_roles()
    return service


@pytest.fixture
def api_key_provider(session_factory, registry):
    """The real provider, not a stand-in — issue #170 is precisely the case
    where the seeding test and the provider test each passed alone while the
    credential they agree on could never authenticate."""
    from flux.security.providers.api_key import APIKeyProvider

    return APIKeyProvider(session_factory=session_factory, registry=registry)


def _key_rows(session_factory, principal_id):
    from flux.security.models import APIKeyModel

    session = session_factory()
    try:
        return session.query(APIKeyModel).filter_by(principal_id=principal_id).all()
    finally:
        session.close()


class TestEnsureAdminKey:
    async def test_first_run_seeds_admin_with_hashed_key_and_0600_file(
        self,
        auth_service,
        registry,
        session_factory,
        tmp_path,
    ):
        key = await admin_bootstrap.ensure_admin_key(
            auth_service,
            registry,
            session_factory,
            tmp_path,
        )

        assert key is not None
        principal = registry.find(admin_bootstrap.ADMIN_SUBJECT, admin_bootstrap.ADMIN_ISSUER)
        assert principal is not None
        assert "admin" in registry.get_roles(principal.id)

        # Only the hash is stored, and it matches the delivered plaintext.
        rows = _key_rows(session_factory, principal.id)
        assert len(rows) == 1
        assert rows[0].key_hash == hashlib.sha256(key.encode()).hexdigest()
        assert key not in rows[0].key_hash

        key_file = tmp_path / admin_bootstrap.ADMIN_KEY_FILENAME
        assert key_file.read_text().strip() == key
        assert stat.S_IMODE(key_file.stat().st_mode) == 0o600
        assert admin_bootstrap.read_persisted(tmp_path) == key

    async def test_second_run_is_a_noop(self, auth_service, registry, session_factory, tmp_path):
        first = await admin_bootstrap.ensure_admin_key(
            auth_service,
            registry,
            session_factory,
            tmp_path,
        )
        second = await admin_bootstrap.ensure_admin_key(
            auth_service,
            registry,
            session_factory,
            tmp_path,
        )
        assert first is not None
        assert second is None, "restarts must never re-mint"
        # The delivered file still holds the first key.
        assert admin_bootstrap.read_persisted(tmp_path) == first

    async def test_existing_admin_principal_suppresses_seeding(
        self,
        auth_service,
        registry,
        session_factory,
        tmp_path,
    ):
        operator = registry.create(
            type="user",
            subject="ops@example.com",
            external_issuer="https://idp.example.com",
        )
        registry.assign_role(operator.id, "admin")

        seeded = await admin_bootstrap.ensure_admin_key(
            auth_service,
            registry,
            session_factory,
            tmp_path,
        )
        assert seeded is None
        assert not (tmp_path / admin_bootstrap.ADMIN_KEY_FILENAME).exists()
        assert registry.find(admin_bootstrap.ADMIN_SUBJECT, admin_bootstrap.ADMIN_ISSUER) is None

    async def test_disabled_admin_does_not_count(
        self,
        auth_service,
        registry,
        session_factory,
        tmp_path,
    ):
        stale = registry.create(
            type="user",
            subject="gone@example.com",
            external_issuer="https://idp.example.com",
        )
        registry.assign_role(stale.id, "admin")
        registry.set_enabled(stale.id, False)

        seeded = await admin_bootstrap.ensure_admin_key(
            auth_service,
            registry,
            session_factory,
            tmp_path,
        )
        assert seeded is not None, "a disabled admin is not a usable admin"

    async def test_partial_prior_run_is_repaired(
        self,
        auth_service,
        registry,
        session_factory,
        tmp_path,
    ):
        """A crash between principal creation and key minting must not brick
        the bootstrap: the principal is reused and a key is minted."""
        registry.create(
            type="user",
            subject=admin_bootstrap.ADMIN_SUBJECT,
            external_issuer=admin_bootstrap.ADMIN_ISSUER,
        )  # exists, but holds no role and no key

        key = await admin_bootstrap.ensure_admin_key(
            auth_service,
            registry,
            session_factory,
            tmp_path,
        )
        assert key is not None
        principal = registry.find(admin_bootstrap.ADMIN_SUBJECT, admin_bootstrap.ADMIN_ISSUER)
        assert "admin" in registry.get_roles(principal.id)
        assert len(_key_rows(session_factory, principal.id)) == 1


class TestRotate:
    async def test_rotate_replaces_key_and_file(
        self,
        auth_service,
        registry,
        session_factory,
        tmp_path,
    ):
        first = await admin_bootstrap.ensure_admin_key(
            auth_service,
            registry,
            session_factory,
            tmp_path,
        )
        second = await admin_bootstrap.rotate(auth_service, registry, tmp_path)

        assert second != first
        principal = registry.find(admin_bootstrap.ADMIN_SUBJECT, admin_bootstrap.ADMIN_ISSUER)
        rows = _key_rows(session_factory, principal.id)
        assert len(rows) == 1, "the previous key hash must be gone"
        assert rows[0].key_hash == hashlib.sha256(second.encode()).hexdigest()
        assert admin_bootstrap.read_persisted(tmp_path) == second

    async def test_rotate_bootstraps_from_nothing(
        self,
        auth_service,
        registry,
        session_factory,
        tmp_path,
    ):
        """--rotate on a fresh install mints the principal and key outright."""
        key = await admin_bootstrap.rotate(auth_service, registry, tmp_path)
        assert key is not None
        principal = registry.find(admin_bootstrap.ADMIN_SUBJECT, admin_bootstrap.ADMIN_ISSUER)
        assert "admin" in registry.get_roles(principal.id)
        assert admin_bootstrap.read_persisted(tmp_path) == key


class TestBootstrapKeyAuthenticates:
    """Issue #170: the seeding test and the provider test each passed alone
    while the minted key could never authenticate — the bootstrap principal
    was 'user'-typed and the api-key provider only accepts service accounts.
    These tests drive the bootstrap key through the *real* provider."""

    async def test_bootstrap_key_authenticates_through_api_key_provider(
        self,
        auth_service,
        registry,
        session_factory,
        tmp_path,
    ):
        from flux.security.providers.api_key import APIKeyProvider

        key = await admin_bootstrap.ensure_admin_key(
            auth_service,
            registry,
            session_factory,
            tmp_path,
        )
        assert key is not None

        identity = await APIKeyProvider(session_factory, registry=registry).authenticate(key)
        assert identity is not None, "bootstrap key must authenticate through the real provider"
        assert identity.subject == admin_bootstrap.ADMIN_SUBJECT
        assert "admin" in identity.roles

        principal = registry.find(admin_bootstrap.ADMIN_SUBJECT, admin_bootstrap.ADMIN_ISSUER)
        assert principal.type == "service_account"

    async def test_rotate_repairs_user_typed_principal_from_broken_deployment(
        self,
        auth_service,
        registry,
        session_factory,
        tmp_path,
    ):
        """A deployment seeded by the pre-#170 implementation holds a
        'user'-typed bootstrap principal with a dead key; rotating must flip
        the type and mint a key that authenticates."""
        from flux.security.providers.api_key import APIKeyProvider

        broken = registry.create(
            type="user",
            subject=admin_bootstrap.ADMIN_SUBJECT,
            external_issuer=admin_bootstrap.ADMIN_ISSUER,
        )
        registry.assign_role(broken.id, admin_bootstrap.ADMIN_ROLE, assigned_by="test")
        dead_key = await auth_service.create_api_key(broken.id, admin_bootstrap.ADMIN_KEY_NAME)
        provider = APIKeyProvider(session_factory, registry=registry)
        assert await provider.authenticate(dead_key) is None  # the #170 symptom

        fresh = await admin_bootstrap.rotate(auth_service, registry, tmp_path)
        identity = await provider.authenticate(fresh)
        assert identity is not None and "admin" in identity.roles
        assert (
            registry.find(admin_bootstrap.ADMIN_SUBJECT, admin_bootstrap.ADMIN_ISSUER).type
            == "service_account"
        )
        # The dead key was revoked, not left as a second credential.
        assert await provider.authenticate(dead_key) is None

    async def test_key_resolves_to_admin_permissions_through_auth_service(
        self,
        enabled_auth_service,
        registry,
        session_factory,
        tmp_path,
    ):
        """The issue's repro end to end: mint, then present the key the way a
        request does. Goes through the whole provider chain and on into
        permission resolution, so a key that authenticates but resolves to no
        permissions would fail here too."""
        key = await admin_bootstrap.ensure_admin_key(
            enabled_auth_service,
            registry,
            session_factory,
            tmp_path,
        )

        identity = await enabled_auth_service.authenticate(key)
        permissions = await enabled_auth_service.resolve_permissions(identity)

        assert identity.subject == admin_bootstrap.ADMIN_SUBJECT
        assert "*" in permissions


class TestStartupHealsBrokenDeployment:
    """Repair has to reach the *startup* path, not just rotation.

    A deployment seeded before #170 holds a broken principal that still
    carries the admin role, so ``has_admin_principal`` is satisfied and
    ``ensure_admin_key`` returns before it would ever mint. Unless the repair
    runs ahead of that guard, restarting the server changes nothing and the
    operator has to diagnose the 401s and rotate by hand.
    """

    async def _seed_pre_fix_deployment(self, auth_service, registry):
        principal = registry.create(
            type="user",
            subject=admin_bootstrap.ADMIN_SUBJECT,
            external_issuer=admin_bootstrap.ADMIN_ISSUER,
            display_name="Bootstrap admin",
            metadata={"via": "admin-bootstrap"},
        )
        registry.assign_role(principal.id, admin_bootstrap.ADMIN_ROLE, assigned_by="test")
        key = await auth_service.create_api_key(principal.id, admin_bootstrap.ADMIN_KEY_NAME)
        return principal, key

    async def test_restart_makes_the_existing_key_work(
        self,
        auth_service,
        registry,
        session_factory,
        api_key_provider,
        tmp_path,
    ):
        _, key = await self._seed_pre_fix_deployment(auth_service, registry)
        assert await api_key_provider.authenticate(key) is None, (
            "guard for the bug being fixed: the pre-#170 credential is rejected"
        )

        # Exactly what the server's lifespan hook calls on every start.
        seeded = await admin_bootstrap.ensure_admin_key(
            auth_service,
            registry,
            session_factory,
            tmp_path,
        )

        assert seeded is None, "an admin already exists — seeding stays init-only"
        identity = await api_key_provider.authenticate(key)
        assert identity is not None, "restarting the server must heal the deployment"
        assert admin_bootstrap.ADMIN_ROLE in identity.roles

    async def test_repair_does_not_remint(
        self,
        auth_service,
        registry,
        session_factory,
        tmp_path,
    ):
        """The plaintext only exists in the operator's copy of the file, so
        minting a replacement would invalidate it for no reason."""
        principal, key = await self._seed_pre_fix_deployment(auth_service, registry)
        before = _key_rows(session_factory, principal.id)[0].key_hash

        await admin_bootstrap.ensure_admin_key(auth_service, registry, session_factory, tmp_path)

        rows = _key_rows(session_factory, principal.id)
        assert len(rows) == 1
        assert rows[0].key_hash == before == hashlib.sha256(key.encode()).hexdigest()

    async def test_repair_leaves_other_user_principals_alone(
        self,
        auth_service,
        registry,
        session_factory,
        tmp_path,
    ):
        """Only the bootstrap identity is re-typed. An OIDC-provisioned human
        stays a ``user``, so a static API key can never be minted for them."""
        human = registry.create(
            type="user",
            subject="ops@example.com",
            external_issuer="https://idp.example.com",
        )
        registry.assign_role(human.id, admin_bootstrap.ADMIN_ROLE)

        await admin_bootstrap.ensure_admin_key(auth_service, registry, session_factory, tmp_path)

        assert registry.get(human.id).type == "user"
