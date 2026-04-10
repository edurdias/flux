from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from flux.models import Base


@pytest.fixture
def engine():
    import flux.security  # noqa: F401 — ensure PrincipalModel is registered
    import flux.security.models  # noqa: F401 — ensure RoleModel and APIKeyModel are registered
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture
def session(engine):
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()


def test_principal_model_exists():
    from flux.security.principals import PrincipalModel
    p = PrincipalModel(
        type="user",
        subject="alice@acme.com",
        external_issuer="https://auth.example.com",
        display_name="Alice",
    )
    assert p.subject == "alice@acme.com"
    assert p.enabled is True


def test_principal_role_model_exists():
    from flux.security.principals import PrincipalRoleModel
    pr = PrincipalRoleModel(
        principal_id="abc123",
        role_name="operator",
        assigned_by="admin",
    )
    assert pr.role_name == "operator"


class TestPrincipalRegistry:
    @pytest.fixture
    def registry(self, session):
        from flux.security.principals import PrincipalRegistry
        from flux.security.models import RoleModel

        role = RoleModel(name="operator", permissions=["workflow:*:run"], built_in=False)
        session.add(role)
        session.commit()
        return PrincipalRegistry(session_factory=lambda: session)

    def test_create_and_find(self, registry):
        p = registry.create(
            type="user",
            subject="alice@acme.com",
            external_issuer="https://auth.example.com",
            display_name="Alice",
            metadata={},
        )
        assert p.id is not None
        found = registry.find("alice@acme.com", "https://auth.example.com")
        assert found is not None
        assert found.id == p.id

    def test_find_returns_none_for_missing(self, registry):
        result = registry.find("nobody@example.com", "https://auth.example.com")
        assert result is None

    def test_assign_and_get_roles(self, registry):
        p = registry.create(
            type="service_account",
            subject="svc-pipeline",
            external_issuer="flux",
            display_name="Pipeline SA",
            metadata={},
        )
        registry.assign_role(p.id, "operator", assigned_by="admin")
        roles = registry.get_roles(p.id)
        assert "operator" in roles

    def test_revoke_role(self, registry):
        p = registry.create(
            type="service_account",
            subject="svc-worker",
            external_issuer="flux",
            display_name="Worker SA",
            metadata={},
        )
        registry.assign_role(p.id, "operator", assigned_by="admin")
        registry.revoke_role(p.id, "operator")
        roles = registry.get_roles(p.id)
        assert "operator" not in roles

    def test_set_enabled(self, registry):
        p = registry.create(
            type="user",
            subject="bob@acme.com",
            external_issuer="https://auth.example.com",
            display_name="Bob",
            metadata={},
        )
        registry.set_enabled(p.id, False)
        found = registry.find("bob@acme.com", "https://auth.example.com")
        assert found.enabled is False

    def test_update_last_seen(self, registry):
        p = registry.create(
            type="user",
            subject="carol@acme.com",
            external_issuer="https://auth.example.com",
            display_name="Carol",
            metadata={},
        )
        assert p.last_seen_at is None
        registry.update_last_seen(p.id)
        found = registry.find("carol@acme.com", "https://auth.example.com")
        assert found.last_seen_at is not None

    def test_update_metadata(self, registry):
        p = registry.create(
            type="user",
            subject="dave@acme.com",
            external_issuer="https://auth.example.com",
            display_name="Dave",
            metadata={"locale": "en"},
        )
        registry.update_metadata(p.id, display_name="David", metadata={"locale": "fr"})
        found = registry.find("dave@acme.com", "https://auth.example.com")
        assert found.display_name == "David"
        assert found.metadata_["locale"] == "fr"


def test_api_key_model_has_principal_id(engine, session):
    from flux.security.principals import PrincipalModel
    from flux.security.models import APIKeyModel

    p = PrincipalModel(
        type="service_account",
        subject="svc-test",
        external_issuer="flux",
        display_name="Test SA",
    )
    session.add(p)
    session.commit()

    key = APIKeyModel(
        principal_id=p.id,
        name="default",
        key_hash="abc123",
        key_prefix="flux_sk_abc",
    )
    session.add(key)
    session.commit()

    assert key.principal_id == p.id
    assert key.principal.subject == "svc-test"


def test_migration_creates_principals_table():
    from sqlalchemy import create_engine, inspect as sa_inspect
    import flux.security  # noqa: F401 — ensure PrincipalModel is registered
    import flux.security.models  # noqa: F401 — ensure RoleModel and APIKeyModel are registered

    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)

    inspector = sa_inspect(eng)
    assert "principals" in inspector.get_table_names()
    assert "principal_roles" in inspector.get_table_names()

    col_names = [c["name"] for c in inspector.get_columns("principals")]
    assert "subject" in col_names
    assert "external_issuer" in col_names
    assert "enabled" in col_names
    assert "last_seen_at" in col_names

    key_cols = [c["name"] for c in inspector.get_columns("api_keys")]
    assert "principal_id" in key_cols
