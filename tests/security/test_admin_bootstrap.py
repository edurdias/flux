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
