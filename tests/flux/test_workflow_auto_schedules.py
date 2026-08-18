"""Lifecycle of the `<workflow>_auto` schedule created from
`@workflow.with_options(schedule=...)` (issue #254).

The auto-schedule was the precedent outbound hooks' owner-scoped
reconciliation was modelled on, but it had neither a real
create-or-replace across versions nor any delete-with-workflow, and no
test pinned either. These tests cover both halves of that lifecycle;
they mirror the structure of tests/flux/test_workflow_hooks_registration.py,
with auth off (permission enforcement for registration and deletion is
covered in tests/security/).
"""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient

from flux.config import Configuration


@pytest.fixture
def db(tmp_path):
    Configuration.get().override(database_url=f"sqlite:///{tmp_path / 'auto_sched.db'}")
    from flux.models import DatabaseRepository

    DatabaseRepository._engines.clear()
    yield
    DatabaseRepository._engines.clear()


@pytest.fixture
def server_instance(db):
    from flux.server import Server

    return Server(host="localhost", port=8000)


@pytest.fixture
def client(server_instance):
    return TestClient(server_instance._create_api())


def _upload(client, source: str, filename: str = "wf.py"):
    return client.post(
        "/workflows",
        files={"file": (filename, io.BytesIO(source.encode()), "text/x-python")},
    )


def _schedules(client) -> list[dict]:
    resp = client.get("/schedules", params={"active_only": False})
    assert resp.status_code == 200, resp.text
    return resp.json()


_UNSCHEDULED_SOURCE = """
from flux import ExecutionContext, workflow


@workflow.with_options(namespace="ops")
async def manual_sync(ctx: ExecutionContext):
    return ctx.input
"""

_SOURCE = """
from flux import ExecutionContext, workflow
from flux.domain.schedule import interval


@workflow.with_options(namespace="ops", schedule=interval(hours=6))
async def nightly_sync(ctx: ExecutionContext):
    return ctx.input
"""


class TestAutoScheduleLifecycle:
    def test_registering_a_scheduled_workflow_creates_the_auto_schedule(self, client):
        resp = _upload(client, _SOURCE)

        assert resp.status_code == 200, resp.text
        schedules = _schedules(client)
        assert len(schedules) == 1
        assert schedules[0]["name"] == "nightly_sync_auto"
        assert schedules[0]["workflow_name"] == "nightly_sync"

    def test_reregistering_replaces_the_auto_schedule_instead_of_duplicating_it(self, client):
        # Every registration writes a new workflows row (a new version, a
        # new id), but the auto-schedule is derived from the workflow's
        # *name* -- so a redeploy must land on the same schedule, not add
        # a second row that fires the same workflow twice per tick.
        _upload(client, _SOURCE)
        first_id = _schedules(client)[0]["id"]

        resp = _upload(client, _SOURCE)

        assert resp.status_code == 200, resp.text
        schedules = _schedules(client)
        assert len(schedules) == 1
        assert schedules[0]["id"] == first_id

    def test_reregistering_with_a_changed_schedule_updates_the_row(self, client):
        _upload(client, _SOURCE)
        first_id = _schedules(client)[0]["id"]

        changed = _SOURCE.replace(
            "from flux.domain.schedule import interval",
            "from flux.domain.schedule import cron",
        ).replace("interval(hours=6)", 'cron("0 3 * * *")')
        resp = _upload(client, changed)

        assert resp.status_code == 200, resp.text
        schedules = _schedules(client)
        assert len(schedules) == 1
        assert schedules[0]["id"] == first_id
        assert schedules[0]["schedule_type"] == "cron"

    def test_reregistering_with_a_changed_overlap_policy_updates_the_row(self, client):
        # overlap_policy is a derived column the scheduler's overlap check
        # reads (issue #142) -- an update that only rewrote the pickled
        # schedule object left the policy in force at its old value.
        _upload(client, _SOURCE)
        assert _schedules(client)[0]["overlap_policy"] == "skip"

        changed = _SOURCE.replace("interval(hours=6)", 'interval(hours=6, overlap="allow")')
        resp = _upload(client, changed)

        assert resp.status_code == 200, resp.text
        assert _schedules(client)[0]["overlap_policy"] == "allow"

    def test_reregistering_repoints_the_auto_schedule_at_the_new_version(self, client):
        # The FK is version-scoped, so a schedule left pointing at the
        # previous version dies with it -- and blocks that version's
        # deletion in the meantime.
        _upload(client, _SOURCE)
        _upload(client, _SOURCE)

        from flux.catalogs import WorkflowCatalog

        latest = WorkflowCatalog.create().get("ops", "nightly_sync")
        assert _schedules(client)[0]["workflow_id"] == latest.id

    def test_deleting_the_workflows_only_version_deletes_its_auto_schedule(self, client):
        _upload(client, _SOURCE)
        assert len(_schedules(client)) == 1

        resp = client.delete("/workflows/ops/nightly_sync")

        assert resp.status_code == 200, resp.text
        assert _schedules(client) == []

    def test_deleting_one_of_several_versions_keeps_the_auto_schedule(self, client):
        _upload(client, _SOURCE)  # v1
        _upload(client, _SOURCE)  # v2, same declaration
        assert len(_schedules(client)) == 1

        resp = client.delete("/workflows/ops/nightly_sync?version=1")

        assert resp.status_code == 200, resp.text
        assert len(_schedules(client)) == 1


class TestOperatorCreatedScheduleLifecycle:
    """Operator-created schedules share the FK, so they share its fate."""

    def _create_schedule(self, client):
        return client.post(
            "/schedules",
            json={
                "workflow_name": "manual_sync",
                "workflow_namespace": "ops",
                "name": "nightly",
                "schedule_config": {"type": "cron", "cron_expression": "0 3 * * *"},
            },
        )

    def test_deleting_the_workflow_deletes_its_operator_created_schedule(self, client):
        _upload(client, _UNSCHEDULED_SOURCE)
        assert self._create_schedule(client).status_code == 200
        assert len(_schedules(client)) == 1

        resp = client.delete("/workflows/ops/manual_sync")

        assert resp.status_code == 200, resp.text
        assert _schedules(client) == []
