"""Banned principals: quarantine that worker registration respects (C1).

``enabled`` alone cannot express quarantine — the reaper disables pruned
workers' principals and registration re-enables them when the worker
returns. ``banned`` is the explicit operator state: worker registration
refuses it (even with a valid bootstrap or join token), and a banned
principal cannot be enabled until it is unbanned.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from flux.models import Base
from flux.security.principals import PrincipalRegistry


BOOTSTRAP = "test-bootstrap-secret"


@pytest.fixture
def registry():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield PrincipalRegistry(session_factory=lambda: session)
    session.close()


class TestSetBanned:
    def test_ban_disables_and_records_reason(self, registry):
        p = registry.create(type="service_account", subject="w1", external_issuer="flux")
        registry.set_banned(p.id, True, reason="failed recertification")

        banned = registry.get(p.id)
        assert banned.banned is True
        assert banned.enabled is False
        assert banned.metadata_["ban_reason"] == "failed recertification"

    def test_unban_clears_reason_but_stays_disabled(self, registry):
        p = registry.create(type="service_account", subject="w1", external_issuer="flux")
        registry.set_banned(p.id, True, reason="quarantine")
        registry.set_banned(p.id, False)

        unbanned = registry.get(p.id)
        assert unbanned.banned is False
        assert unbanned.enabled is False
        assert "ban_reason" not in (unbanned.metadata_ or {})

    def test_enable_banned_principal_rejected(self, registry):
        p = registry.create(type="service_account", subject="w1", external_issuer="flux")
        registry.set_banned(p.id, True)

        with pytest.raises(ValueError, match="banned"):
            registry.set_enabled(p.id, True)

    def test_enable_after_unban_allowed(self, registry):
        p = registry.create(type="service_account", subject="w1", external_issuer="flux")
        registry.set_banned(p.id, True)
        registry.set_banned(p.id, False)
        registry.set_enabled(p.id, True)

        assert registry.get(p.id).enabled is True

    def test_new_principals_are_not_banned(self, registry):
        p = registry.create(type="service_account", subject="w1", external_issuer="flux")
        assert p.banned is False


@pytest.fixture
def make_client(tmp_path, monkeypatch):
    """Factory: build a FluxServer TestClient backed by a file-based SQLite DB."""
    db_path = tmp_path / "ban.db"
    monkeypatch.setenv("FLUX_DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("FLUX_WORKERS__BOOTSTRAP_TOKEN", BOOTSTRAP)

    from flux.config import Configuration
    from flux.models import DatabaseRepository

    Configuration._instance = None  # type: ignore[attr-defined]
    Configuration._config = None  # type: ignore[attr-defined]
    DatabaseRepository._engines.clear()
    Configuration.get().override(database_url=f"sqlite:///{db_path}")

    def _make():
        from flux.server import Server

        settings = Configuration.get().settings
        server = Server("127.0.0.1", 0)
        app = server._create_api()
        # TestClient without a context manager never runs the lifespan, which
        # is where the server resolves its bootstrap token — seed it the way
        # resolve_or_generate would for a configured value.
        server._bootstrap_token = settings.workers.bootstrap_token
        return TestClient(app)

    yield _make

    Configuration._instance = None  # type: ignore[attr-defined]
    Configuration._config = None  # type: ignore[attr-defined]
    DatabaseRepository._engines.clear()


def _registration_body(name: str) -> dict:
    return {
        "name": name,
        "runtime": {"os_name": "Linux", "os_version": "6", "python_version": "3.12"},
        "packages": [],
        "resources": {
            "cpu_total": 1,
            "cpu_available": 1,
            "memory_total": 1,
            "memory_available": 1,
            "disk_total": 1,
            "disk_free": 1,
            "gpus": [],
        },
    }


def _register(client, name: str, token: str = BOOTSTRAP):
    return client.post(
        "/workers/register",
        json=_registration_body(name),
        headers={"Authorization": f"Bearer {token}"},
    )


def _server_registry() -> PrincipalRegistry:
    from flux.models import RepositoryFactory

    repo = RepositoryFactory.create_repository()
    return PrincipalRegistry(session_factory=repo.session)


class TestBannedRegistration:
    def test_banned_principal_cannot_register(self, make_client):
        client = make_client()
        assert _register(client, "w1").status_code == 200

        registry = _server_registry()
        principal = registry.find(subject="w1", external_issuer="flux")
        if principal is None:
            # API-key auth disabled in this harness: no auto-provisioned
            # principal, so create the row the ban applies to.
            principal = registry.create(
                type="service_account",
                subject="w1",
                external_issuer="flux",
            )
        registry.set_banned(principal.id, True, reason="quarantined")

        resp = _register(client, "w1")
        assert resp.status_code == 403
        assert "banned" in resp.json()["detail"]

    def test_unban_restores_registration(self, make_client):
        client = make_client()
        assert _register(client, "w1").status_code == 200

        registry = _server_registry()
        principal = registry.find(subject="w1", external_issuer="flux") or registry.create(
            type="service_account",
            subject="w1",
            external_issuer="flux",
        )
        registry.set_banned(principal.id, True)
        assert _register(client, "w1").status_code == 403

        registry.set_banned(principal.id, False)
        assert _register(client, "w1").status_code == 200

    def test_ban_of_one_worker_does_not_affect_others(self, make_client):
        client = make_client()
        registry = _server_registry()
        principal = registry.create(
            type="service_account",
            subject="w1",
            external_issuer="flux",
        )
        registry.set_banned(principal.id, True)

        assert _register(client, "w1").status_code == 403
        assert _register(client, "w2").status_code == 200


class TestAdminBanRoutes:
    def test_ban_unban_roundtrip(self, make_client):
        client = make_client()
        registry = _server_registry()
        registry.create(type="service_account", subject="w1", external_issuer="flux")

        resp = client.post(
            "/admin/principals/w1/ban",
            json={"reason": "failed recert"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["banned"] is True

        principal = registry.find(subject="w1", external_issuer="flux")
        assert principal.banned is True
        assert principal.enabled is False
        assert principal.metadata_["ban_reason"] == "failed recert"

        resp = client.post("/admin/principals/w1/unban")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["banned"] is False
        # Unban does not re-enable; that is an explicit follow-up action.
        assert body["enabled"] is False

    def test_enable_banned_principal_is_409(self, make_client):
        client = make_client()
        registry = _server_registry()
        principal = registry.create(type="service_account", subject="w1", external_issuer="flux")
        registry.set_banned(principal.id, True)

        resp = client.post("/admin/principals/w1/enable")
        assert resp.status_code == 409
        assert "banned" in resp.json()["detail"]

    def test_ban_unknown_principal_is_404(self, make_client):
        client = make_client()
        assert client.post("/admin/principals/ghost/ban").status_code == 404

    def test_principal_payloads_include_banned(self, make_client):
        client = make_client()
        registry = _server_registry()
        principal = registry.create(type="service_account", subject="w1", external_issuer="flux")
        registry.set_banned(principal.id, True)

        listed = client.get("/admin/principals").json()
        assert any(p["subject"] == "w1" and p["banned"] is True for p in listed)

        shown = client.get("/admin/principals/w1").json()
        assert shown["banned"] is True
