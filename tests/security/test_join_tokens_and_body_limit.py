"""One-time worker join tokens (SEC3) and the global body-size cap (SEC5).

Join tokens replace the fleet-wide bootstrap secret as a per-registration
credential: minted with a TTL, stored hashed, consumed atomically on first
use. The body cap rejects oversized request bodies (declared or streamed)
before they are read into memory.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from flux.security import join_tokens


BOOTSTRAP = "test-bootstrap-secret"


@pytest.fixture
def make_client(tmp_path, monkeypatch):
    """Factory: build a FluxServer TestClient after optional config tweaks."""
    db_path = tmp_path / "join_tokens.db"
    monkeypatch.setenv("FLUX_DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("FLUX_WORKERS__BOOTSTRAP_TOKEN", BOOTSTRAP)

    from flux.config import Configuration
    from flux.models import DatabaseRepository

    Configuration._instance = None  # type: ignore[attr-defined]
    Configuration._config = None  # type: ignore[attr-defined]
    DatabaseRepository._engines.clear()
    Configuration.get().override(database_url=f"sqlite:///{db_path}")

    def _make(**settings_overrides):
        from flux.server import Server

        settings = Configuration.get().settings
        for key, value in settings_overrides.items():
            obj = settings
            *path, leaf = key.split(".")
            for part in path:
                obj = getattr(obj, part)
            setattr(obj, leaf, value)
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


def _register(client, name: str, token: str):
    return client.post(
        "/workers/register",
        json=_registration_body(name),
        headers={"Authorization": f"Bearer {token}"},
    )


class TestJoinTokenLifecycle:
    def test_claim_is_single_use(self, make_client):
        make_client()  # initializes the DB schema
        token, _expires = join_tokens.mint(3600)
        assert join_tokens.claim(token, "w1") is True
        assert join_tokens.claim(token, "w2") is False

    def test_expired_token_rejected(self, make_client):
        make_client()
        token, _expires = join_tokens.mint(3600)

        from flux.models import RepositoryFactory

        repo = RepositoryFactory.create_repository()
        with repo.session() as session:
            session.query(join_tokens.WorkerJoinTokenModel).update(
                {
                    "expires_at": datetime.now(timezone.utc).replace(tzinfo=None)
                    - timedelta(seconds=1),
                },
                synchronize_session=False,
            )
            session.commit()

        assert join_tokens.claim(token, "w1") is False

    def test_unknown_token_rejected(self, make_client):
        make_client()
        assert join_tokens.claim("never-minted", "w1") is False

    def test_mint_rejects_nonpositive_ttl(self, make_client):
        make_client()
        with pytest.raises(ValueError):
            join_tokens.mint(0)

    def test_purge_keeps_recent_and_drops_stale(self, make_client):
        make_client()
        join_tokens.mint(3600)  # live token: kept
        stale_token, _ = join_tokens.mint(3600)

        from flux.models import RepositoryFactory

        repo = RepositoryFactory.create_repository()
        with repo.session() as session:
            session.query(join_tokens.WorkerJoinTokenModel).filter(
                join_tokens.WorkerJoinTokenModel.token_hash == join_tokens._hash(stale_token),
            ).update(
                {
                    "expires_at": datetime.now(timezone.utc).replace(tzinfo=None)
                    - timedelta(days=2),
                },
                synchronize_session=False,
            )
            session.commit()

        assert join_tokens.purge_expired(older_than_seconds=86400) == 1


class TestRegistrationCredentials:
    def test_join_token_registers_and_is_consumed(self, make_client):
        client = make_client()
        token, _ = join_tokens.mint(3600)

        first = _register(client, "worker-jt", token)
        assert first.status_code == 200, first.text

        replay = _register(client, "worker-jt-2", token)
        assert replay.status_code == 403, replay.text

    def test_bootstrap_token_still_works_by_default(self, make_client):
        client = make_client()
        from flux.config import Configuration

        effective = Configuration.get().settings.workers.bootstrap_token
        resp = _register(client, "worker-bt", effective)
        assert resp.status_code == 200, resp.text

    def test_bootstrap_token_can_be_disabled(self, make_client):
        client = make_client(**{"workers.bootstrap_token_enabled": False})
        from flux.config import Configuration

        effective = Configuration.get().settings.workers.bootstrap_token
        rejected = _register(client, "worker-bt-off", effective)
        assert rejected.status_code == 403, rejected.text

        token, _ = join_tokens.mint(3600)
        accepted = _register(client, "worker-jt-only", token)
        assert accepted.status_code == 200, accepted.text

    def test_garbage_token_rejected(self, make_client):
        client = make_client()
        resp = _register(client, "worker-bad", "not-a-real-token")
        assert resp.status_code == 403, resp.text


class TestBodySizeLimit:
    def test_declared_oversize_is_413(self, make_client):
        client = make_client(server_max_body_size=1024)
        resp = client.post("/workflows", content=b"x" * 4096)
        assert resp.status_code == 413, resp.text
        assert "too large" in resp.text.lower()

    def test_streamed_oversize_is_413(self, make_client):
        """No Content-Length (chunked): the streaming counter must enforce
        the cap while the app reads the body. Target a JSON route — parsing
        forces the read (the multipart route 422s without reading)."""
        client = make_client(server_max_body_size=1024)

        def chunks():
            for _ in range(8):
                yield b"y" * 1024

        resp = client.post(
            "/schedules",
            content=chunks(),
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 413, resp.text

    def test_small_bodies_pass_through(self, make_client):
        client = make_client(server_max_body_size=1024)
        # Middleware must not interfere: the request reaches the route
        # (422/400 from validation, not 413).
        resp = client.post("/workflows", content=b"tiny")
        assert resp.status_code != 413

    def test_zero_disables_the_limit(self, make_client):
        client = make_client(server_max_body_size=0)
        resp = client.post("/workflows", content=b"z" * 4096)
        assert resp.status_code != 413
