"""Route-level tests for the hooks REST surface.

The operator surface over the hook registry: CRUD, a synthetic test fire, and
the deliveries ops endpoints. Auth is off here (as in the rest of
``tests/flux/``), so the permission strings themselves are exercised in
``tests/security/test_hook_authz.py``; what these tests pin down is the
routes' own behaviour — status codes, the shape of the response rows, and the
guards that must run before a row is written.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from flux.config import Configuration
from flux.server import Server


@pytest.fixture
def db(tmp_path):
    Configuration.get().override(database_url=f"sqlite:///{tmp_path / 'hooks.db'}")
    from flux.models import DatabaseRepository

    DatabaseRepository._engines.clear()
    yield
    DatabaseRepository._engines.clear()


@pytest.fixture
def server_instance(db):
    return Server(host="localhost", port=8000)


@pytest.fixture
def client(server_instance):
    return TestClient(server_instance._create_api())


def _seed_workflow(namespace: str, name: str) -> str:
    from flux.models import RepositoryFactory, WorkflowModel

    repo = RepositoryFactory.create_repository()
    workflow_id = f"{namespace}/{name}"
    with repo.session() as session:
        session.add(
            WorkflowModel(
                id=workflow_id,
                name=name,
                version=1,
                imports=[],
                source=b"async def p(ctx): pass",
                namespace=namespace,
            ),
        )
        session.commit()
    return workflow_id


def _payload(**overrides):
    payload = {
        "name": "on-fail",
        "selectors": ["execution:*:*:failed"],
        "workflow_ref": "ops/incident",
        "principal": "p-1",
    }
    payload.update(overrides)
    return payload


def _create(client, **overrides):
    return client.post("/hooks", json=_payload(**overrides))


def _seed_delivery(hook_id: str, *, event_key: str, status: str, created_at: datetime, **fields):
    from flux.models import HookDeliveryModel, RepositoryFactory

    repo = RepositoryFactory.create_repository()
    with repo.session() as session:
        delivery = HookDeliveryModel(
            hook_id=hook_id,
            event_key=event_key,
            payload={"hook": "on-fail"},
            status=status,
            created_at=created_at,
            **fields,
        )
        session.add(delivery)
        session.commit()
        session.refresh(delivery)
        return delivery.id


@pytest.fixture
def target_workflow():
    return _seed_workflow("ops", "incident")


class TestCreate:
    def test_create_returns_the_row(self, client, target_workflow):
        resp = _create(client)

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["name"] == "on-fail"
        assert body["selectors"] == ["execution:*:*:failed"]
        assert body["workflow_ref"] == "ops/incident"
        assert body["principal"] == "p-1"
        assert body["enabled"] is True
        assert body["action"] == "run_workflow"
        assert body["max_attempts"] == 5
        assert body["owner_type"] == "user"
        assert body["id"] and body["created_at"] and body["updated_at"]

    def test_invalid_selector_is_400_naming_it(self, client, target_workflow):
        resp = _create(client, selectors=["execution:*:*:failed", "carrier:pigeon"])

        assert resp.status_code == 400, resp.text
        assert "carrier:pigeon" in resp.text
        assert client.get("/hooks").json()["total"] == 0

    def test_malformed_workflow_ref_is_400(self, client):
        resp = _create(client, workflow_ref="a/b/c")

        assert resp.status_code == 400, resp.text
        assert "a/b/c" in resp.text

    def test_unknown_target_workflow_is_404(self, client):
        resp = _create(client, workflow_ref="ops/nowhere")

        assert resp.status_code == 404, resp.text
        assert "ops/nowhere" in resp.text

    def test_principal_that_cannot_run_the_target_is_403(
        self,
        client,
        server_instance,
        target_workflow,
    ):
        """The create-time half of fire-time authorization: a hook whose stored
        principal cannot run its target is a hook that only ever dead-letters."""
        with patch.object(server_instance, "_authorize_hook", new=AsyncMock(return_value=False)):
            resp = _create(client)

        assert resp.status_code == 403, resp.text
        assert "workflow:ops:incident:run" in resp.text
        assert client.get("/hooks").json()["total"] == 0

    def test_duplicate_name_is_409(self, client, target_workflow):
        assert _create(client).status_code == 200

        resp = _create(client, workflow_ref="ops/incident")

        assert resp.status_code == 409, resp.text
        assert "on-fail" in resp.text


class TestReadUpdateDelete:
    def test_round_trip(self, client, target_workflow):
        _seed_workflow("ops", "page")
        assert _create(client).status_code == 200

        listed = client.get("/hooks")
        assert listed.status_code == 200, listed.text
        assert listed.json()["total"] == 1
        assert [h["name"] for h in listed.json()["hooks"]] == ["on-fail"]

        got = client.get("/hooks/on-fail")
        assert got.status_code == 200, got.text
        assert got.json()["workflow_ref"] == "ops/incident"

        updated = client.put(
            "/hooks/on-fail",
            json={
                "enabled": False,
                "selectors": ["execution:release:*:paused"],
                "workflow_ref": "ops/page",
                "max_attempts": 2,
            },
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["enabled"] is False
        assert updated.json()["selectors"] == ["execution:release:*:paused"]
        assert updated.json()["workflow_ref"] == "ops/page"
        assert updated.json()["max_attempts"] == 2

        deleted = client.delete("/hooks/on-fail")
        assert deleted.status_code == 200, deleted.text
        assert client.get("/hooks/on-fail").status_code == 404
        assert client.get("/hooks").json()["total"] == 0

    def test_list_can_be_filtered_to_enabled_hooks(self, client, target_workflow):
        assert _create(client).status_code == 200
        assert _create(client, name="off").status_code == 200
        assert client.put("/hooks/off", json={"enabled": False}).status_code == 200

        listed = client.get("/hooks", params={"enabled_only": True})

        assert [h["name"] for h in listed.json()["hooks"]] == ["on-fail"]

    def test_get_of_a_missing_hook_is_404(self, client):
        assert client.get("/hooks/ghost").status_code == 404

    def test_update_of_a_missing_hook_is_404(self, client):
        assert client.put("/hooks/ghost", json={"enabled": False}).status_code == 404

    def test_delete_of_a_missing_hook_is_404(self, client):
        assert client.delete("/hooks/ghost").status_code == 404

    def test_update_rejects_an_invalid_selector(self, client, target_workflow):
        assert _create(client).status_code == 200

        resp = client.put("/hooks/on-fail", json={"selectors": ["task:too:short"]})

        assert resp.status_code == 400, resp.text
        assert "task:too:short" in resp.text
        assert client.get("/hooks/on-fail").json()["selectors"] == ["execution:*:*:failed"]

    def test_retargeting_to_a_principal_that_cannot_run_is_403(
        self,
        client,
        server_instance,
        target_workflow,
    ):
        assert _create(client).status_code == 200

        with patch.object(server_instance, "_authorize_hook", new=AsyncMock(return_value=False)):
            resp = client.put("/hooks/on-fail", json={"principal": "p-2"})

        assert resp.status_code == 403, resp.text
        assert client.get("/hooks/on-fail").json()["principal"] == "p-1"

    def test_disabling_a_hook_does_not_re_check_the_principal(
        self,
        client,
        server_instance,
        target_workflow,
    ):
        """`enabled=false` is the stop button. Refusing it because the stored
        principal has since lost the permission would jam the one control an
        operator reaches for when a hook is misbehaving."""
        assert _create(client).status_code == 200

        with patch.object(server_instance, "_authorize_hook", new=AsyncMock(return_value=False)):
            resp = client.put("/hooks/on-fail", json={"enabled": False})

        assert resp.status_code == 200, resp.text
        assert resp.json()["enabled"] is False


class TestTestFire:
    def test_starts_the_target_with_a_synthetic_envelope(self, client, target_workflow):
        from flux.context_managers import ContextManager

        assert (
            _create(client, selectors=["task:release:*:promote:awaiting_approval"]).status_code
            == 200
        )

        resp = client.post("/hooks/on-fail/test")

        assert resp.status_code == 200, resp.text
        body = resp.json()
        execution_id = body["execution_id"]
        assert execution_id

        ctx = ContextManager.create().get(execution_id)
        assert ctx.workflow_namespace == "ops"
        assert ctx.workflow_name == "incident"
        envelope = ctx.input
        assert envelope["hook"] == "on-fail"
        assert envelope["selector"] == "task:release:*:promote:awaiting_approval"
        assert envelope["hop"] == 0
        assert envelope["attempt"] == 1
        assert envelope["event"]["domain"] == "task"
        assert envelope["event"]["type"] == "awaiting_approval"
        assert envelope["event"]["task_name"] == "promote"
        assert envelope["event"]["workflow_namespace"] == "release"
        assert body["envelope"] == envelope

    def test_writes_no_delivery_row(self, client, target_workflow):
        from flux.models import HookDeliveryModel, RepositoryFactory

        assert _create(client).status_code == 200
        assert client.post("/hooks/on-fail/test").status_code == 200

        with RepositoryFactory.create_repository().session() as session:
            assert session.query(HookDeliveryModel).count() == 0

    def test_test_of_a_missing_hook_is_404(self, client):
        assert client.post("/hooks/ghost/test").status_code == 404

    def test_a_principal_that_can_no_longer_run_the_target_is_403(
        self,
        client,
        server_instance,
        target_workflow,
    ):
        """The test fire starts a real execution as the hook's principal, so it
        takes the same fire-time check the drain does — otherwise it is a way
        to run a workflow as a principal that may no longer run it."""
        from flux.models import ExecutionContextModel, RepositoryFactory

        assert _create(client).status_code == 200

        with patch.object(server_instance, "_authorize_hook", new=AsyncMock(return_value=False)):
            resp = client.post("/hooks/on-fail/test")

        assert resp.status_code == 403, resp.text
        assert "workflow:ops:incident:run" in resp.text
        with RepositoryFactory.create_repository().session() as session:
            assert session.query(ExecutionContextModel).count() == 0

    def test_target_deleted_after_creation_is_409(self, client, target_workflow):
        from flux.models import RepositoryFactory, WorkflowModel

        assert _create(client).status_code == 200
        with RepositoryFactory.create_repository().session() as session:
            session.query(WorkflowModel).delete()
            session.commit()

        resp = client.post("/hooks/on-fail/test")

        assert resp.status_code == 409, resp.text
        assert "ops/incident" in resp.text


class TestDeliveries:
    def test_returned_newest_first_and_limited(self, client, target_workflow):
        hook_id = _create(client).json()["id"]
        base = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)
        for index in range(3):
            _seed_delivery(
                hook_id,
                event_key=f"ev-{index}",
                status="pending",
                created_at=base + timedelta(minutes=index),
            )

        resp = client.get("/hooks/on-fail/deliveries")
        assert resp.status_code == 200, resp.text
        assert [d["event_key"] for d in resp.json()] == ["ev-2", "ev-1", "ev-0"]

        limited = client.get("/hooks/on-fail/deliveries", params={"limit": 2})
        assert [d["event_key"] for d in limited.json()] == ["ev-2", "ev-1"]

    def test_only_this_hooks_deliveries_are_listed(self, client, target_workflow):
        mine = _create(client).json()["id"]
        theirs = _create(client, name="other").json()["id"]
        now = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)
        _seed_delivery(mine, event_key="mine", status="pending", created_at=now)
        _seed_delivery(theirs, event_key="theirs", status="pending", created_at=now)

        resp = client.get("/hooks/on-fail/deliveries")

        assert [d["event_key"] for d in resp.json()] == ["mine"]

    def test_deliveries_of_a_missing_hook_is_404(self, client):
        assert client.get("/hooks/ghost/deliveries").status_code == 404

    def test_retry_resets_a_dead_row(self, client, target_workflow):
        hook_id = _create(client).json()["id"]
        delivery_id = _seed_delivery(
            hook_id,
            event_key="ev-dead",
            status="dead",
            created_at=datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc),
            attempts=5,
            last_error="gave up after 5 attempt(s)",
            next_attempt_at=datetime(2026, 8, 14, 12, 5, tzinfo=timezone.utc),
        )

        resp = client.post(f"/hooks/on-fail/deliveries/{delivery_id}/retry")

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "pending"
        assert body["attempts"] == 0
        assert body["next_attempt_at"] is None
        assert body["last_error"] is None

    def test_retry_of_a_live_row_is_409(self, client, target_workflow):
        hook_id = _create(client).json()["id"]
        delivery_id = _seed_delivery(
            hook_id,
            event_key="ev-pending",
            status="pending",
            created_at=datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc),
        )

        resp = client.post(f"/hooks/on-fail/deliveries/{delivery_id}/retry")

        assert resp.status_code == 409, resp.text

    def test_retry_of_another_hooks_delivery_is_404(self, client, target_workflow):
        theirs = _create(client, name="other").json()["id"]
        assert _create(client).status_code == 200
        delivery_id = _seed_delivery(
            theirs,
            event_key="ev-theirs",
            status="dead",
            created_at=datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc),
        )

        resp = client.post(f"/hooks/on-fail/deliveries/{delivery_id}/retry")

        assert resp.status_code == 404, resp.text
