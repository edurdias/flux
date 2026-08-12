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


class TestSubjectBinding:
    """A token minted for one worker must not authorize another (#174).

    Without binding, any live token authorizes registration under any name,
    and the registration path revokes the incumbent's API keys — so a holder
    of their own token can evict a worker and inherit admin-written metadata
    attached to its subject.
    """

    def test_bound_token_rejects_a_different_worker(self, make_client):
        make_client()
        token, _ = join_tokens.mint(3600, subject="worker-a")
        assert join_tokens.claim(token, "worker-b") is False

    def test_bound_token_accepts_its_own_worker(self, make_client):
        make_client()
        token, _ = join_tokens.mint(3600, subject="worker-a")
        assert join_tokens.claim(token, "worker-a") is True

    def test_rejected_claim_does_not_consume_the_token(self, make_client):
        """A failed claim must leave the token live, or an attacker who
        guesses wrong burns a legitimate worker's credential."""
        make_client()
        token, _ = join_tokens.mint(3600, subject="worker-a")
        assert join_tokens.claim(token, "worker-b") is False
        assert join_tokens.claim(token, "worker-a") is True

    def test_unbound_token_still_claims_for_any_worker(self, make_client):
        """Rows minted before binding existed have a NULL subject and must
        keep working, so an upgrade does not strand a mid-flight token."""
        make_client()
        token, _ = join_tokens.mint(3600)
        assert join_tokens.claim(token, "any-worker") is True

    def test_registration_rejects_token_bound_to_another_worker(self, make_client):
        client = make_client()
        token, _ = join_tokens.mint(3600, subject="worker-a")

        impostor = _register(client, "worker-b", token)
        assert impostor.status_code == 403, impostor.text

        legitimate = _register(client, "worker-a", token)
        assert legitimate.status_code == 200, legitimate.text


class TestMintRoute:
    def test_explicit_zero_ttl_is_rejected(self, make_client):
        """ttl_seconds: 0 must be a 400 from mint(), not silently replaced
        by the default TTL."""
        client = make_client()
        resp = client.post("/admin/workers/join-tokens", json={"ttl_seconds": 0})
        assert resp.status_code == 400, resp.text

    def test_omitted_ttl_uses_default(self, make_client):
        client = make_client()
        resp = client.post("/admin/workers/join-tokens", json={})
        assert resp.status_code == 200, resp.text
        assert resp.json()["token"]

    def test_minted_subject_binds_the_token(self, make_client):
        """Binding is only reachable if the operator-facing route exposes it;
        otherwise every token minted through the API stays unbound."""
        client = make_client()
        resp = client.post("/admin/workers/join-tokens", json={"subject": "worker-a"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["subject"] == "worker-a"

        token = resp.json()["token"]
        assert join_tokens.claim(token, "worker-b") is False
        assert join_tokens.claim(token, "worker-a") is True

    def test_omitted_subject_mints_an_unbound_token(self, make_client):
        client = make_client()
        resp = client.post("/admin/workers/join-tokens", json={})
        assert resp.status_code == 200, resp.text
        assert resp.json()["subject"] is None
        assert join_tokens.claim(resp.json()["token"], "any-worker") is True

    def test_blank_subject_is_rejected(self, make_client):
        """An empty string must not silently mint an unbound token when the
        operator's intent was clearly to bind one."""
        client = make_client()
        resp = client.post("/admin/workers/join-tokens", json={"subject": "   "})
        assert resp.status_code == 400, resp.text

    @pytest.mark.parametrize("value", [123, True, {"a": 1}, ["worker-a"], 1.5])
    def test_non_string_subject_is_rejected(self, make_client, value):
        """subject decides which identity a token authorizes, so a non-string
        must be refused rather than coerced. str() would turn {"a": 1} into
        the Python repr "{'a': 1}" and bind the token to a name no worker can
        ever present — an operator would read the 200 as a successful bind."""
        client = make_client()
        resp = client.post("/admin/workers/join-tokens", json={"subject": value})
        assert resp.status_code == 400, resp.text

    def test_null_subject_mints_unbound(self, make_client):
        """JSON null reads as 'not provided', matching an omitted field."""
        client = make_client()
        resp = client.post("/admin/workers/join-tokens", json={"subject": None})
        assert resp.status_code == 200, resp.text
        assert resp.json()["subject"] is None


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


class TestJoinTokenCLI:
    """`flux server join-token` is the documented way to mint, so it has to
    reach the binding too — otherwise operators keep minting unbound tokens.
    Asserts against a real claim, not a call-args mock."""

    def test_subject_option_binds_the_token(self, make_client):
        from click.testing import CliRunner

        from flux.cli import cli

        make_client()  # initializes the DB schema on the temp database
        result = CliRunner().invoke(cli, ["server", "join-token", "--subject", "worker-a"])
        assert result.exit_code == 0, result.output

        token = result.stdout.strip().splitlines()[0]
        assert join_tokens.claim(token, "worker-b") is False
        assert join_tokens.claim(token, "worker-a") is True

    def test_without_subject_mints_an_unbound_token(self, make_client):
        from click.testing import CliRunner

        from flux.cli import cli

        make_client()
        result = CliRunner().invoke(cli, ["server", "join-token"])
        assert result.exit_code == 0, result.output

        token = result.stdout.strip().splitlines()[0]
        assert join_tokens.claim(token, "any-worker") is True


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


class TestJoinTokenRevocation:
    """Minting had no inverse (issue #197): a token left live by a failed
    bring-up stayed claimable until its TTL, invisible and unretirable."""

    def test_outstanding_lists_live_tokens_without_the_secret(self, make_client):
        make_client()
        token, _ = join_tokens.mint(3600, subject="worker-a", created_by="test")

        rows = join_tokens.outstanding()

        assert [r["subject"] for r in rows] == ["worker-a"]
        assert token not in str(rows), "the plaintext must never be recoverable"
        assert "token_hash" not in rows[0], "the hash is credential-equivalent"

    def test_revoked_token_cannot_register(self, make_client):
        client = make_client()
        token, _ = join_tokens.mint(3600, subject="worker-a")

        assert join_tokens.revoke(join_tokens.outstanding()[0]["id"]) is True

        assert _register(client, "worker-a", token).status_code == 403

    def test_revoking_by_subject_retires_every_token_for_that_worker(self, make_client):
        """The shape that pairs with a ban: the caller knows the worker name
        and does not track token ids."""
        make_client()
        join_tokens.mint(3600, subject="worker-a")
        join_tokens.mint(3600, subject="worker-a")
        join_tokens.mint(3600, subject="worker-b")

        assert join_tokens.revoke_for_subject("worker-a") == 2

        assert [r["subject"] for r in join_tokens.outstanding()] == ["worker-b"]

    def test_revoking_by_subject_leaves_unbound_tokens_alone(self, make_client):
        """An unbound token carries no subject, so retiring it under one
        worker's name would take out a credential meant for another."""
        make_client()
        join_tokens.mint(3600)  # unbound
        join_tokens.mint(3600, subject="worker-a")

        assert join_tokens.revoke_for_subject("worker-a") == 1

        assert [r["subject"] for r in join_tokens.outstanding()] == [None]

    def test_revoking_a_spent_token_reports_nothing_to_do(self, make_client):
        client = make_client()
        token, _ = join_tokens.mint(3600, subject="worker-a")
        token_id = join_tokens.outstanding()[0]["id"]
        assert _register(client, "worker-a", token).status_code == 200

        assert join_tokens.revoke(token_id) is False
        assert join_tokens.revoke("no-such-id") is False

    def test_a_banned_worker_does_not_burn_the_token(self, make_client):
        """claim() consumes the token in a committed UPDATE, so checking the
        ban afterwards let a banned holder spend a credential the operator
        would have to mint again. The ban check runs first."""
        from flux.models import RepositoryFactory
        from flux.security.principals import PrincipalRegistry

        client = make_client()
        repo = RepositoryFactory.create_repository()
        registry = PrincipalRegistry(session_factory=lambda: repo.session())
        principal = registry.create(
            type="service_account",
            subject="worker-banned",
            external_issuer="flux",
        )
        registry.set_banned(principal.id, True)
        token, _ = join_tokens.mint(3600, subject="worker-banned")

        assert _register(client, "worker-banned", token).status_code == 403

        assert [r["subject"] for r in join_tokens.outstanding()] == ["worker-banned"], (
            "the token must survive a refused registration"
        )
