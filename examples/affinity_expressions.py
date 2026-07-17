"""Affinity expressions: per-execution worker targeting with require(...).

A static ``affinity={"gpu": "a100"}`` dict pins a workflow to one fixed
label set. An expression built with ``flux.routing.require(...)`` keeps the
same hard-filter role but resolves its terms against each execution's input
at dispatch — so one registered workflow serves many differently-routed
requests:

    {"dc": "eu-central", "dataset": "orders-2026"}   -> workers in that
        datacenter holding a local copy of the dataset
    {..., "node": "node-a"}                          -> pinned to one machine
    {..., "classification": "restricted"}            -> gated to
        compliance-certified workers

Affinity only takes effect on the distributed path (``flux start server`` +
labeled workers); inline runs execute in-process. Running this file inline
still exercises the full registration path — the catalog statically
extracts the expression from source, and an unparseable one fails loudly.
"""

from __future__ import annotations

from flux import ExecutionContext, task, workflow
from flux.routing import (
    input,
    label,
    label_for,
    least,
    load,
    optional,
    prefer,
    require,
    score,
    when,
)


@task
async def run_query(dataset: str, query: str) -> dict:
    # A real deployment would query the worker-local dataset copy here.
    return {"dataset": dataset, "rows": [f"result({query})"]}


@workflow.with_options(
    affinity=require(
        label("datacenter") == input("dc"),  # data locality
        label_for("dataset.", input("dataset")) == "true",  # worker holds a local copy
        optional(label("node") == input("node")),  # hard pin only when requested
        when(  # compliance gate on requester intent
            input("classification") == "restricted",
            label("compliance.hipaa") == "true",
        ),
    ),
    # The same vocabulary ranks the eligible workers: require() is the hard
    # floor, prefer() the soft preference — here, favor a warm cache copy
    # without excluding cold workers, then break ties by load.
    routing=score(
        prefer(label_for("cache.", input("dataset")) == "true", weight=5),
        least(load()),
    ),
)
async def locality_query(ctx: ExecutionContext[dict]):
    """Run a query on a worker co-located with the requested dataset."""
    request = ctx.input or {}
    return await run_query(request.get("dataset", ""), request.get("query", ""))


@workflow.with_options(affinity=require(label("tenant") == input("tenant_id")))
async def tenant_report(ctx: ExecutionContext[dict]):
    """Tenant isolation on a shared fleet: each tenant's executions only
    reach workers labeled for that tenant."""
    tenant = (ctx.input or {}).get("tenant_id")
    return {"tenant": tenant, "report": "ok"}


@workflow.with_options(affinity=require(label("maintenance") != "true"))
async def nightly_cleanup(ctx: ExecutionContext):
    """Maintenance windows without redeploying: label a worker
    maintenance=true to drain it; unlabeled workers keep matching."""
    return {"cleaned": True}


if __name__ == "__main__":  # pragma: no cover
    ctx = locality_query.run(
        {"dc": "eu-central", "dataset": "orders-2026", "query": "count(*)"},
    )
    print(ctx.to_json())

    ctx = tenant_report.run({"tenant_id": "acme"})
    print(ctx.to_json())

    ctx = nightly_cleanup.run()
    print(ctx.to_json())
