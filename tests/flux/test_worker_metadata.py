"""Server-side, admin-writable worker metadata (issue #138).

Covers the validation rule, the registry CRUD (including survival across
re-registration — the property that makes the channel authoritative), the
meta(...) selector in require()/score() evaluation, the dispatch-time DB
refresh (admin writes land on the next dispatch without a reconnect), and
the /admin/workers/{name}/metadata routes.
"""

from __future__ import annotations

import pytest

from flux.errors import WorkerNotFoundError
from flux.routing import (
    MAX_METADATA_KEYS,
    label,
    least,
    load,
    meta,
    metric,
    most,
    pick_worker,
    prefer,
    require,
    require_matches,
    score,
    validate_worker_metadata,
)
from flux.worker_registry import WorkerInfo
from tests.flux.test_dispatch_batch import (
    _register_worker,
    clean_env,  # noqa: F401 - pytest fixture
)


def _create_workflow(name, affinity=None, routing=None, namespace="default"):
    from flux.models import RepositoryFactory, WorkflowModel

    repo = RepositoryFactory.create_repository()
    with repo.session() as session:
        wf = WorkflowModel(
            id=f"{namespace}/{name}",
            name=name,
            version=1,
            imports=[],
            source=b"async def placeholder(ctx): pass",
            namespace=namespace,
            affinity=affinity,
            metadata={"routing": routing} if routing is not None else None,
        )
        session.add(wf)
        session.commit()
        return wf.id


class TestValidation:
    def test_values_normalize_to_str_or_float(self):
        result = validate_worker_metadata({"weight": 5, "tier": "gold", "drain": True})
        assert result == {"weight": 5.0, "tier": "gold", "drain": "true"}

    def test_false_becomes_the_label_convention_string(self):
        assert validate_worker_metadata({"drain": False}) == {"drain": "false"}

    @pytest.mark.parametrize(
        "payload",
        [
            "not-a-dict",
            {"": 1},
            {"bad key!": 1},
            {"k" * 65: 1},
            {"inf": float("inf")},
            {"nan": float("nan")},
            {"long": "v" * 257},
            {"obj": {"nested": 1}},
            {k: 1 for k in (f"key{i}" for i in range(MAX_METADATA_KEYS + 1))},
        ],
    )
    def test_invalid_payloads_raise(self, payload):
        with pytest.raises(ValueError):
            validate_worker_metadata(payload)


class TestDsl:
    def test_meta_in_require_terms(self):
        terms = require(meta("maintenance") != "true", label("region") == "eu")
        assert terms[0] == {
            "kind": "match",
            "selector": "meta:maintenance",
            "op": "!=",
            "value": "true",
        }

    def test_meta_in_score_terms(self):
        policy = score(most(meta("weight")), prefer(meta("tier") == "gold"))
        assert policy["terms"][0]["selector"] == "meta:weight"
        assert policy["terms"][1]["selector"] == "meta:tier"

    def test_metric_still_rejected_in_require(self):
        with pytest.raises(ValueError, match="metric\\(\\)"):
            require(metric("fitness") == 1)


class TestRequireMatches:
    def test_meta_term_reads_metadata_not_labels(self):
        terms = require(meta("tier") == "gold")
        # The same key as a label must not satisfy a meta term — the channels
        # are disjoint, which is what makes metadata unspoofable.
        assert not require_matches(terms, {"tier": "gold"}, None)
        assert require_matches(terms, {}, None, worker_metadata={"tier": "gold"})

    def test_absent_key_fails_eq_and_passes_ne(self):
        assert not require_matches(require(meta("tier") == "gold"), {}, None)
        assert require_matches(require(meta("maintenance") != "true"), {}, None)

    def test_numeric_metadata_compares_in_string_form(self):
        terms = require(meta("weight") == "5.0")
        assert require_matches(terms, {}, None, worker_metadata={"weight": 5.0})


class TestPickWorker:
    def test_most_meta_ranks_numeric_metadata(self):
        w1 = WorkerInfo("w1", metadata={"weight": 1.0})
        w2 = WorkerInfo("w2", metadata={"weight": 9.0})
        policy = score(most(meta("weight"), weight=10), least(load()))
        assert pick_worker([w1, w2], policy, loads={"w1": 0, "w2": 0}).name == "w2"

    def test_absent_metadata_scores_zero(self):
        w1 = WorkerInfo("w1")
        w2 = WorkerInfo("w2", metadata={"weight": 0.1})
        policy = score(most(meta("weight")))
        assert pick_worker([w1, w2], policy, loads={"w1": 0, "w2": 0}).name == "w2"

    def test_prefer_meta_equality(self):
        w1 = WorkerInfo("w1", metadata={"tier": "silver"})
        w2 = WorkerInfo("w2", metadata={"tier": "gold"})
        policy = score(prefer(meta("tier") == "gold", weight=5))
        assert pick_worker([w1, w2], policy, loads={"w1": 0, "w2": 0}).name == "w2"


class TestRegistry:
    def test_set_merges_and_returns_result(self, clean_env):  # noqa: F811
        _cm, registry = clean_env
        _register_worker(registry, "w1")
        assert registry.set_metadata("w1", {"a": "1"}) == {"a": "1"}
        assert registry.set_metadata("w1", {"b": 2.0}) == {"a": "1", "b": 2.0}
        assert registry.get("w1").metadata == {"a": "1", "b": 2.0}

    def test_replace_swaps_the_dict(self, clean_env):  # noqa: F811
        _cm, registry = clean_env
        _register_worker(registry, "w1")
        registry.set_metadata("w1", {"a": "1", "b": "2"})
        assert registry.set_metadata("w1", {"c": "3"}, replace=True) == {"c": "3"}

    def test_delete_key_is_idempotent_and_clear_empties(self, clean_env):  # noqa: F811
        _cm, registry = clean_env
        _register_worker(registry, "w1")
        registry.set_metadata("w1", {"a": "1", "b": "2"})
        assert registry.delete_metadata("w1", "a") == {"b": "2"}
        assert registry.delete_metadata("w1", "missing") == {"b": "2"}
        assert registry.delete_metadata("w1", None) == {}
        assert registry.get("w1").metadata is None

    def test_unknown_worker_raises(self, clean_env):  # noqa: F811
        _cm, registry = clean_env
        with pytest.raises(WorkerNotFoundError):
            registry.set_metadata("ghost", {"a": "1"})
        with pytest.raises(WorkerNotFoundError):
            registry.delete_metadata("ghost", "a")

    def test_metadata_survives_re_registration(self, clean_env):  # noqa: F811
        _cm, registry = clean_env
        _register_worker(registry, "w1")
        registry.set_metadata("w1", {"tier": "gold"})
        # A worker reconnecting re-registers with fresh labels; the
        # admin-written channel must come through untouched.
        _register_worker(registry, "w1", labels={"gpu": "true"})
        info = registry.get("w1")
        assert info.labels == {"gpu": "true"}
        assert info.metadata == {"tier": "gold"}

    def test_merge_cannot_exceed_the_key_cap(self, clean_env):  # noqa: F811
        _cm, registry = clean_env
        _register_worker(registry, "w1")
        registry.set_metadata("w1", {f"key{i}": 1.0 for i in range(MAX_METADATA_KEYS)})
        with pytest.raises(ValueError, match="limited"):
            registry.set_metadata("w1", {"one-more": 1.0})


class TestDispatch:
    def _create_execution(self, cm, workflow_id, name, input_value=None):
        from flux.domain.execution_context import ExecutionContext

        ctx = ExecutionContext(
            workflow_id=workflow_id,
            workflow_namespace="default",
            workflow_name=name,
            input=input_value,
        )
        return cm.save(ctx)

    def test_require_meta_gates_dispatch_and_hot_updates(self, clean_env):  # noqa: F811
        cm, registry = clean_env
        worker = _register_worker(registry, "w1")
        _create_workflow("gated", affinity=require(meta("allowed") == "true"))
        ctx = self._create_execution(cm, "default/gated", "gated")

        # No metadata yet: the worker is not eligible, the row stays queued.
        assert cm.next_executions_batch([worker], limit=10) == []

        # Admin write, then the SAME in-memory WorkerInfo (stale snapshot, as
        # held by the dispatcher) must match on the next batch — the dispatch
        # transaction re-reads the column.
        registry.set_metadata("w1", {"allowed": "true"})
        assignments = cm.next_executions_batch([worker], limit=10)
        assert [(c.execution_id, w) for c, w in assignments] == [(ctx.execution_id, "w1")]

    def test_score_meta_ranks_from_fresh_db_values(self, clean_env):  # noqa: F811
        cm, registry = clean_env
        w1 = _register_worker(registry, "w1")
        w2 = _register_worker(registry, "w2")
        registry.set_metadata("w2", {"weight": 9.0})
        # Stale in-memory value pointing the other way must lose to the DB.
        w1.metadata = {"weight": 99.0}
        _create_workflow("weighted", routing=score(most(meta("weight"))))
        ctx = self._create_execution(cm, "default/weighted", "weighted")

        assignments = cm.next_executions_batch([w1, w2], limit=10)
        assert [(c.execution_id, w) for c, w in assignments] == [(ctx.execution_id, "w2")]


class TestAdminRoutes:
    @pytest.fixture
    def client_env(self, tmp_path):
        from fastapi.testclient import TestClient

        from flux.config import Configuration
        from flux.server import Server
        from flux.worker_registry import DatabaseWorkerRegistry

        Configuration.get().override(database_url=f"sqlite:///{tmp_path / 'meta.db'}")
        from flux.models import DatabaseRepository

        DatabaseRepository._engines.clear()
        server = Server("127.0.0.1", 0)
        client = TestClient(server._create_api())
        registry = DatabaseWorkerRegistry()
        from tests.flux.test_dispatch_batch import _make_resources, _make_runtime

        registry.register(
            name="w1",
            runtime=_make_runtime(),
            packages=[],
            resources=_make_resources(),
        )
        yield client, server, registry
        DatabaseRepository._engines.clear()

    def test_put_merges_and_returns_result(self, client_env):
        client, _server, registry = client_env
        resp = client.put(
            "/admin/workers/w1/metadata",
            json={"metadata": {"tier": "gold", "weight": 5}},
        )
        assert resp.status_code == 200
        assert resp.json() == {"metadata": {"tier": "gold", "weight": 5.0}}

        resp = client.put("/admin/workers/w1/metadata", json={"metadata": {"drain": True}})
        assert resp.json()["metadata"] == {"tier": "gold", "weight": 5.0, "drain": "true"}

        resp = client.put(
            "/admin/workers/w1/metadata",
            json={"metadata": {"only": "this"}, "replace": True},
        )
        assert resp.json()["metadata"] == {"only": "this"}
        assert registry.get("w1").metadata == {"only": "this"}

    def test_get_reads_back_and_delete_removes(self, client_env):
        client, _server, _registry = client_env
        client.put("/admin/workers/w1/metadata", json={"metadata": {"a": "1", "b": "2"}})

        assert client.get("/admin/workers/w1/metadata").json() == {
            "metadata": {"a": "1", "b": "2"},
        }
        assert client.delete("/admin/workers/w1/metadata/a").json() == {"metadata": {"b": "2"}}
        # Idempotent: deleting an absent key is not an error.
        assert client.delete("/admin/workers/w1/metadata/a").status_code == 200
        assert client.delete("/admin/workers/w1/metadata").json() == {"metadata": {}}

    def test_unknown_worker_is_404(self, client_env):
        client, _server, _registry = client_env
        assert client.get("/admin/workers/ghost/metadata").status_code == 404
        assert (
            client.put("/admin/workers/ghost/metadata", json={"metadata": {"a": "1"}}).status_code
            == 404
        )
        assert client.delete("/admin/workers/ghost/metadata/a").status_code == 404

    def test_invalid_payload_is_400(self, client_env):
        client, _server, _registry = client_env
        assert (
            client.put("/admin/workers/w1/metadata", json={"metadata": {"bad key!": 1}}).status_code
            == 400
        )
        assert client.put("/admin/workers/w1/metadata", json={}).status_code == 400

    def test_write_refreshes_in_memory_copies(self, client_env):
        client, server, registry = client_env
        # Simulate a connected worker: the dispatcher's snapshot and the
        # response cache both live in server memory.
        info = registry.get("w1")
        server._worker_info["w1"] = info
        from flux.api.schemas import WorkerResponse

        server._worker_cache["w1"] = WorkerResponse(name="w1")

        client.put("/admin/workers/w1/metadata", json={"metadata": {"tier": "gold"}})
        assert info.metadata == {"tier": "gold"}
        assert server._worker_cache["w1"].metadata == {"tier": "gold"}

        client.delete("/admin/workers/w1/metadata")
        assert info.metadata is None
        assert server._worker_cache["w1"].metadata is None

    def test_workers_list_surfaces_metadata(self, client_env):
        client, _server, _registry = client_env
        client.put("/admin/workers/w1/metadata", json={"metadata": {"tier": "gold"}})
        (worker,) = client.get("/workers").json()
        assert worker["metadata"] == {"tier": "gold"}


class TestCatalogExtraction:
    def _parse(self, source: bytes):
        from flux.catalogs import WorkflowCatalog

        return WorkflowCatalog.create().parse(source)

    def test_meta_extracted_in_require_and_score(self):
        source = b"""
from flux import workflow
from flux.routing import require, score, meta, most, prefer

@workflow.with_options(
    affinity=require(meta("maintenance") != "true"),
    routing=score(most(meta("weight")), prefer(meta("tier") == "gold")),
)
async def routed(ctx):
    return ctx.input
"""
        (info,) = self._parse(source)
        assert info.affinity == [
            {"kind": "match", "selector": "meta:maintenance", "op": "!=", "value": "true"},
        ]
        routing = info.metadata["routing"]
        assert routing["terms"][0]["selector"] == "meta:weight"
        assert routing["terms"][1]["selector"] == "meta:tier"

    def test_invalid_meta_key_fails_registration(self):
        source = b"""
from flux import workflow
from flux.routing import require, meta

@workflow.with_options(affinity=require(meta("") == "x"))
async def broken(ctx):
    return ctx.input
"""
        with pytest.raises(SyntaxError, match="meta"):
            self._parse(source)
