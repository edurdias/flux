"""Batch dispatch honors declarative routing policies (score stage)."""

from __future__ import annotations

from flux.domain.execution_context import ExecutionContext
from flux.models import RepositoryFactory
from flux.routing import input as input_ref
from flux.routing import least, most, prefer, score, sticky
from tests.flux.test_dispatch_batch import (
    _register_worker,
    clean_env,  # noqa: F401 - pytest fixture
)


def _create_routed_workflow(name, routing=None, namespace="default", affinity=None):
    from flux.models import WorkflowModel

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
            metadata={"routing": routing} if routing else None,
        )
        session.add(wf)
        session.commit()
        return wf.id


def _create_execution(cm, workflow_id, name, input_value=None, preferred_worker=None):
    ctx = ExecutionContext(
        workflow_id=workflow_id,
        workflow_namespace="default",
        workflow_name=name,
        input=input_value,
    )
    return cm.save(ctx, preferred_worker=preferred_worker)


def test_policy_routes_by_worker_metric(clean_env):  # noqa: F811
    cm, registry = clean_env
    weak = _register_worker(registry, "weak-w")
    strong = _register_worker(registry, "strong-w")
    weak.metrics = {"fitness": 0.2}
    strong.metrics = {"fitness": 0.9}
    wf_id = _create_routed_workflow("fit", routing=score(most("metric:fitness", weight=10)))
    ctx = _create_execution(cm, wf_id, "fit")

    assignments = cm.next_executions_batch([weak, strong], limit=10)

    assert [(c.execution_id, w) for c, w in assignments] == [(ctx.execution_id, "strong-w")]


def test_policy_routes_by_execution_input(clean_env):  # noqa: F811
    cm, registry = clean_env
    gold = _register_worker(registry, "gold-w", labels={"tier": "gold"})
    silver = _register_worker(registry, "silver-w", labels={"tier": "silver"})
    policy = score(prefer("label:tier", "==", input_ref("tier"), weight=10), least("load"))
    wf_id = _create_routed_workflow("tiered", routing=policy)
    to_gold = _create_execution(cm, wf_id, "tiered", input_value={"tier": "gold"})
    to_silver = _create_execution(cm, wf_id, "tiered", input_value={"tier": "silver"})

    assignments = {c.execution_id: w for c, w in cm.next_executions_batch([gold, silver], limit=10)}

    assert assignments[to_gold.execution_id] == "gold-w"
    assert assignments[to_silver.execution_id] == "silver-w"


def test_policy_overrides_sticky_hint_unless_opted_in(clean_env):  # noqa: F811
    cm, registry = clean_env
    a = _register_worker(registry, "a-w")
    b = _register_worker(registry, "b-w")
    a.metrics = {"fitness": 0.9}
    b.metrics = {"fitness": 0.1}

    # Policy without sticky(): the hint (b-w) is ignored, fitness wins.
    wf_id = _create_routed_workflow("owns-score", routing=score(most("metric:fitness")))
    hinted = _create_execution(cm, wf_id, "owns-score", preferred_worker="b-w")
    assignments = {c.execution_id: w for c, w in cm.next_executions_batch([a, b], limit=10)}
    assert assignments[hinted.execution_id] == "a-w"

    # Policy with a dominant sticky(): the hint participates and wins.
    wf2_id = _create_routed_workflow(
        "opted-in",
        routing=score(sticky(weight=10), most("metric:fitness")),
    )
    hinted2 = _create_execution(cm, wf2_id, "opted-in", preferred_worker="b-w")
    assignments = {c.execution_id: w for c, w in cm.next_executions_batch([a, b], limit=10)}
    assert assignments[hinted2.execution_id] == "b-w"


def test_malformed_policy_degrades_to_least_loaded(clean_env):  # noqa: F811
    cm, registry = clean_env
    a = _register_worker(registry, "a-w")
    b = _register_worker(registry, "b-w")
    wf_id = _create_routed_workflow("broken", routing={"terms": [{"kind": "warp"}]})
    ctx = _create_execution(cm, wf_id, "broken")

    assignments = cm.next_executions_batch([a, b], limit=10)

    # Still dispatched — a bad policy must degrade, never strand executions.
    assert [(c.execution_id, w) for c, w in assignments] == [(ctx.execution_id, "a-w")]


def test_policy_ranks_only_eligible_workers(clean_env):  # noqa: F811
    cm, registry = clean_env
    labeled = _register_worker(registry, "labeled-w", labels={"gpu": "true"})
    fit = _register_worker(registry, "fit-w")
    labeled.metrics = {"fitness": 0.1}
    fit.metrics = {"fitness": 0.9}
    # Hard affinity filter excludes fit-w despite its dominant score.
    wf_id = _create_routed_workflow(
        "gated",
        routing=score(most("metric:fitness", weight=10)),
        affinity={"gpu": "true"},
    )
    ctx = _create_execution(cm, wf_id, "gated")

    assignments = cm.next_executions_batch([labeled, fit], limit=10)

    assert [(c.execution_id, w) for c, w in assignments] == [(ctx.execution_id, "labeled-w")]
