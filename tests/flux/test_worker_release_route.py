"""Route-level tests for the release fence.

The release endpoint hands a claimed execution back for re-dispatch. Its
claim-generation fence was opt-in per request — omit the header and any
worker principal could unclaim any dispatched execution — and it never
verified the caller owned the execution at all. The one legitimate
claimless release is a worker declining a dispatch it never claimed
(paused / unhealthy): the claim response is what hands out the generation,
so pre-claim there is nothing to send.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from flux import ExecutionContext
from flux.config import Configuration
from flux.context_managers import ContextManager
from flux.domain.events import ExecutionState
from flux.server import Server


@pytest.fixture
def manager(tmp_path):
    Configuration.get().override(database_url=f"sqlite:///{tmp_path / 'release.db'}")
    from flux.models import DatabaseRepository

    DatabaseRepository._engines.clear()
    yield ContextManager.create()
    DatabaseRepository._engines.clear()


@pytest.fixture
def server_instance(manager):
    return Server(host="localhost", port=8000)


@pytest.fixture
def test_client(server_instance):
    from flux.security.identity import FluxIdentity

    worker_identity = FluxIdentity(subject="w1", roles=frozenset({"worker"}))
    mock_auth = MagicMock()

    async def mock_authenticate(token):
        return worker_identity

    async def mock_is_authorized(identity, permission):
        return True

    mock_auth.authenticate = mock_authenticate
    mock_auth.is_authorized = mock_is_authorized

    with (
        patch.object(server_instance, "_verify_worker_identity"),
        patch.object(server_instance, "_record_heartbeat", new=AsyncMock()),
        patch("flux.security.dependencies._get_auth_service", return_value=mock_auth),
    ):
        yield TestClient(server_instance._create_api())


def _execution(manager, state, worker_name, generation=1):
    from flux.models import ExecutionContextModel, WorkflowModel

    with manager.session() as session:
        if session.get(WorkflowModel, "wf-1") is None:
            session.add(
                WorkflowModel(id="wf-1", name="wf", version=1, imports=[], source=b""),
            )
            session.commit()

    ctx = ExecutionContext(
        workflow_id="wf-1",
        workflow_namespace="default",
        workflow_name="wf",
        input=None,
    )
    manager.save(ctx)
    with manager.session() as session:
        row = session.get(ExecutionContextModel, ctx.execution_id)
        row.state = state
        row.worker_name = worker_name
        row.claim_generation = generation
        session.commit()
    return ctx.execution_id


class TestClaimlessRelease:
    def test_declining_an_unclaimed_dispatch_is_allowed(self, test_client, manager):
        """The paused/unhealthy path: SCHEDULED, assigned here, never
        claimed — no generation exists to send."""
        execution_id = _execution(manager, ExecutionState.SCHEDULED, "w1")

        resp = test_client.post(f"/workers/w1/release/{execution_id}")

        assert resp.status_code == 200
        assert resp.json()["status"] == "released"

    def test_declining_an_unclaimed_resume_dispatch_is_allowed(self, test_client, manager):
        execution_id = _execution(manager, ExecutionState.RESUME_SCHEDULED, "w1")

        resp = test_client.post(f"/workers/w1/release/{execution_id}")

        assert resp.status_code == 200

    def test_claimless_release_of_a_running_execution_is_fenced(self, test_client, manager):
        """Post-claim the worker holds a generation; a release without it is
        either a stale duplicate or a caller sidestepping the fence."""
        execution_id = _execution(manager, ExecutionState.RUNNING, "w1")

        resp = test_client.post(f"/workers/w1/release/{execution_id}")

        assert resp.status_code == 409
        assert "stale-claim" in resp.text
        assert manager.get(execution_id).state == ExecutionState.RUNNING

    def test_release_of_another_workers_execution_is_forbidden(self, test_client, manager):
        """The route verified only that the caller is a worker, not that it
        owns the execution it is releasing."""
        execution_id = _execution(manager, ExecutionState.RUNNING, "w2")

        resp = test_client.post(f"/workers/w1/release/{execution_id}")

        assert resp.status_code == 403
        assert manager.get(execution_id).state == ExecutionState.RUNNING


class TestFencedRelease:
    def test_current_generation_releases(self, test_client, manager):
        execution_id = _execution(manager, ExecutionState.RUNNING, "w1", generation=3)

        resp = test_client.post(
            f"/workers/w1/release/{execution_id}",
            headers={"X-Flux-Claim-Generation": "3"},
        )

        assert resp.status_code == 200
        assert manager.get(execution_id).state == ExecutionState.CREATED

    def test_stale_generation_is_fenced(self, test_client, manager):
        execution_id = _execution(manager, ExecutionState.RUNNING, "w1", generation=3)

        resp = test_client.post(
            f"/workers/w1/release/{execution_id}",
            headers={"X-Flux-Claim-Generation": "2"},
        )

        assert resp.status_code == 409
        assert manager.get(execution_id).state == ExecutionState.RUNNING
