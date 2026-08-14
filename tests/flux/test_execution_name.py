"""Operator-facing execution names.

Writes are a save-time annotation, same treatment as
``preferred_worker``/``required_worker``: ``ExecutionContext.name`` is
read-only, set by ``save(name=...)``/``rename`` and never by workflow code,
and it must survive every state-update save a running workflow makes, not
just the initial one.

Reads are the other half: the agent console surfaces it as a session title,
but a name nothing outside ``/agents/sessions`` could read would be
write-only — so the execution list, both execution read paths and
``flux execution list`` carry it too.
"""

from __future__ import annotations

import pytest

from flux import ExecutionContext
from flux.config import Configuration
from flux.context_managers import ContextManager
from flux.errors import ExecutionContextNotFoundError


@pytest.fixture
def manager(tmp_path):
    Configuration.get().override(database_url=f"sqlite:///{tmp_path / 'names.db'}")
    from flux.models import DatabaseRepository

    DatabaseRepository._engines.clear()
    yield ContextManager.create()
    DatabaseRepository._engines.clear()


def _register_workflow(manager, workflow_id="wf-1"):
    from flux.models import WorkflowModel

    with manager.session() as session:
        session.add(
            WorkflowModel(id=workflow_id, name="named_wf", version=1, imports=[], source=b""),
        )
        session.commit()


def _make_ctx(manager):
    """A CREATED row, as in test_cancellation_sweep._cancelling_execution
    but without the cancel step."""
    _register_workflow(manager)
    ctx = ExecutionContext(
        workflow_id="wf-1",
        workflow_namespace="default",
        workflow_name="named_wf",
        input=None,
    )
    manager.save(ctx)
    return ctx


def _row(manager, execution_id):
    from flux.models import ExecutionContextModel

    with manager.session() as session:
        return session.get(ExecutionContextModel, execution_id)


class TestExecutionName:
    def test_save_persists_name(self, manager):
        ctx = _make_ctx(manager)
        manager.save(ctx, name="fix CI")
        row = _row(manager, ctx.execution_id)
        assert row.name == "fix CI"

    def test_rename(self, manager):
        ctx = _make_ctx(manager)
        manager.rename(ctx.execution_id, "renamed")
        assert _row(manager, ctx.execution_id).name == "renamed"

    def test_rename_missing_execution_raises(self, manager):
        with pytest.raises(ExecutionContextNotFoundError):
            manager.rename("nope", "x")

    def test_name_survives_state_updates(self, manager):
        ctx = _make_ctx(manager)
        manager.save(ctx, name="keep me")
        ctx.start("w1")
        manager.save(ctx)
        assert _row(manager, ctx.execution_id).name == "keep me"

    def test_loaded_context_carries_the_name(self, manager):
        ctx = _make_ctx(manager)
        manager.rename(ctx.execution_id, "fix CI")
        assert manager.get(ctx.execution_id).name == "fix CI"

    def test_unnamed_context_reads_none(self, manager):
        ctx = _make_ctx(manager)
        assert manager.get(ctx.execution_id).name is None

    def test_get_summary_carries_the_name(self, manager):
        """Summary parity with the detailed DTO: the status-poll fast path
        reads columns directly, so it has to select the name too."""
        ctx = _make_ctx(manager)
        manager.rename(ctx.execution_id, "fix CI")
        assert manager.get_summary(ctx.execution_id)["name"] == "fix CI"


@pytest.fixture
def server_instance(manager):
    from flux.server import Server

    return Server(host="localhost", port=8000)


@pytest.fixture
def test_client(server_instance):
    from fastapi.testclient import TestClient

    # Auth is disabled by default (no override below) — every request
    # resolves to ANONYMOUS/admin, same as the rest of the unit suite. No
    # identity mocking needed, unlike the worker-token routes.
    return TestClient(server_instance._create_api())


class TestRenameRoute:
    def test_rename_persists_and_returns_200(self, test_client, manager):
        ctx = _make_ctx(manager)

        resp = test_client.put(
            f"/executions/{ctx.execution_id}/name",
            json={"name": "renamed via API"},
        )

        assert resp.status_code == 200
        assert resp.json() == {
            "execution_id": ctx.execution_id,
            "name": "renamed via API",
        }
        assert _row(manager, ctx.execution_id).name == "renamed via API"

    def test_rename_missing_execution_is_404(self, test_client, manager):
        resp = test_client.put("/executions/nope/name", json={"name": "x"})

        assert resp.status_code == 404

    def test_rename_empty_name_is_400(self, test_client, manager):
        ctx = _make_ctx(manager)

        resp = test_client.put(f"/executions/{ctx.execution_id}/name", json={"name": "   "})

        assert resp.status_code == 400
        # A rejected rename must not clobber whatever name was there before.
        assert _row(manager, ctx.execution_id).name is None

    def test_rename_too_long_name_is_400(self, test_client, manager):
        ctx = _make_ctx(manager)

        resp = test_client.put(
            f"/executions/{ctx.execution_id}/name",
            json={"name": "x" * 201},
        )

        assert resp.status_code == 400

    def test_run_with_name_lands_in_session_list(self, test_client, manager):
        from flux.models import WorkflowModel

        with manager.session() as session:
            session.add(
                WorkflowModel(
                    id="wf-agent",
                    namespace="agents",
                    name="agent_wf",
                    version=1,
                    imports=[],
                    source=b"",
                ),
            )
            session.commit()

        resp = test_client.post(
            "/workflows/agents/agent_wf/run/async",
            params={"name": "session title"},
            json={"agent": "concierge"},
        )

        assert resp.status_code == 200, resp.text
        execution_id = resp.json()["execution_id"]

        sessions_resp = test_client.get("/agents/sessions")
        assert sessions_resp.status_code == 200
        rows = sessions_resp.json()["sessions"]
        row = next(r for r in rows if r["execution_id"] == execution_id)
        assert row["name"] == "session title"

    def test_execution_list_carries_the_name(self, test_client, manager):
        """A name only /agents/sessions could read would be write-only for
        every non-agent execution."""
        named = _make_ctx(manager)
        manager.rename(named.execution_id, "nightly reprocess")

        resp = test_client.get("/executions")

        assert resp.status_code == 200, resp.text
        rows = {row["execution_id"]: row for row in resp.json()["executions"]}
        assert rows[named.execution_id]["name"] == "nightly reprocess"

    def test_execution_list_leaves_an_unnamed_execution_null(self, test_client, manager):
        ctx = _make_ctx(manager)

        resp = test_client.get("/executions")

        rows = {row["execution_id"]: row for row in resp.json()["executions"]}
        assert rows[ctx.execution_id]["name"] is None

    def test_execution_read_carries_the_name(self, test_client, manager):
        ctx = _make_ctx(manager)
        manager.rename(ctx.execution_id, "nightly reprocess")

        summary = test_client.get(f"/executions/{ctx.execution_id}")
        detailed = test_client.get(f"/executions/{ctx.execution_id}", params={"detailed": True})

        assert summary.json()["name"] == "nightly reprocess"
        assert detailed.json()["name"] == "nightly reprocess"

    def test_run_with_too_long_name_is_400(self, test_client, manager):
        from flux.models import WorkflowModel

        with manager.session() as session:
            session.add(
                WorkflowModel(id="wf-2", name="named_wf2", version=1, imports=[], source=b""),
            )
            session.commit()

        resp = test_client.post(
            "/workflows/default/named_wf2/run/async",
            params={"name": "x" * 201},
            json=None,
        )

        assert resp.status_code == 400
