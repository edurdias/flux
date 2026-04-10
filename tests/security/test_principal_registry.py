from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from flux.models import Base
from flux.security.principals import PrincipalModel, PrincipalRegistry, PrincipalRoleModel


@pytest.fixture
def engine():
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture
def session_factory(engine):
    return sessionmaker(bind=engine)


@pytest.fixture
def registry(session_factory):
    return PrincipalRegistry(session_factory=session_factory)


class TestPrincipalModel:
    def test_default_enabled(self):
        p = PrincipalModel(type="user", subject="alice@example.com", external_issuer="https://idp.example.com")
        assert p.enabled is True

    def test_service_account_type(self):
        p = PrincipalModel(type="service_account", subject="svc-ci", external_issuer="flux")
        assert p.type == "service_account"

    def test_id_generated(self):
        p = PrincipalModel(type="user", subject="bob@example.com", external_issuer="flux")
        assert p.id is not None
        assert len(p.id) > 0

    def test_custom_id(self):
        p = PrincipalModel(type="user", subject="carol@example.com", external_issuer="flux", id="custom-id")
        assert p.id == "custom-id"


class TestPrincipalRoleModel:
    def test_role_model_fields(self):
        pr = PrincipalRoleModel(principal_id="pid-1", role_name="operator", assigned_by="admin")
        assert pr.role_name == "operator"
        assert pr.assigned_by == "admin"
        assert pr.assigned_at is not None


class TestPrincipalRegistry:
    def test_create_and_find(self, registry):
        registry.create(type="user", subject="alice@example.com", external_issuer="https://idp.example.com")
        p = registry.find("alice@example.com", "https://idp.example.com")
        assert p is not None
        assert p.subject == "alice@example.com"

    def test_find_returns_none_when_missing(self, registry):
        result = registry.find("nobody@example.com", "flux")
        assert result is None

    def test_get_by_id(self, registry):
        p = registry.create(type="service_account", subject="svc-ci", external_issuer="flux")
        found = registry.get(p.id)
        assert found is not None
        assert found.subject == "svc-ci"

    def test_assign_and_get_roles(self, registry):
        p = registry.create(type="user", subject="bob@example.com", external_issuer="flux")
        registry.assign_role(p.id, "operator")
        registry.assign_role(p.id, "viewer")
        roles = registry.get_roles(p.id)
        assert "operator" in roles
        assert "viewer" in roles

    def test_assign_role_idempotent(self, registry):
        p = registry.create(type="user", subject="carol@example.com", external_issuer="flux")
        registry.assign_role(p.id, "operator")
        registry.assign_role(p.id, "operator")
        roles = registry.get_roles(p.id)
        assert roles.count("operator") == 1

    def test_revoke_role(self, registry):
        p = registry.create(type="user", subject="dave@example.com", external_issuer="flux")
        registry.assign_role(p.id, "operator")
        registry.revoke_role(p.id, "operator")
        roles = registry.get_roles(p.id)
        assert "operator" not in roles

    def test_set_enabled_false(self, registry):
        p = registry.create(type="user", subject="eve@example.com", external_issuer="flux")
        assert p.enabled is True
        registry.set_enabled(p.id, False)
        found = registry.get(p.id)
        assert found.enabled is False

    def test_set_enabled_true(self, registry):
        p = registry.create(type="user", subject="frank@example.com", external_issuer="flux", enabled=False)
        registry.set_enabled(p.id, True)
        found = registry.get(p.id)
        assert found.enabled is True

    def test_update_last_seen(self, registry):
        p = registry.create(type="user", subject="grace@example.com", external_issuer="flux")
        assert p.last_seen_at is None
        registry.update_last_seen(p.id)
        found = registry.get(p.id)
        assert found.last_seen_at is not None

    def test_delete(self, registry):
        p = registry.create(type="service_account", subject="svc-temp", external_issuer="flux")
        registry.delete(p.id)
        assert registry.get(p.id) is None

    def test_unique_constraint_subject_issuer(self, registry):
        registry.create(type="user", subject="shared@example.com", external_issuer="https://idp-a.example.com")
        registry.create(type="user", subject="shared@example.com", external_issuer="https://idp-b.example.com")
        a = registry.find("shared@example.com", "https://idp-a.example.com")
        b = registry.find("shared@example.com", "https://idp-b.example.com")
        assert a.id != b.id
