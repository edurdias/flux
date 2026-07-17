"""Tests for the affinity-expressions example.

Affinity is a dispatch concern; inline execution runs the workflows in the
current process, so these verify the workflows themselves and that the
example's expressions register: each carries a compiled require(...) spec,
and the catalog extracts the same spec statically from the source.
"""

from __future__ import annotations

from pathlib import Path

from examples.affinity_expressions import locality_query, nightly_cleanup, tenant_report

EXAMPLE = Path(__file__).parent.parent.parent / "examples" / "affinity_expressions.py"


def test_locality_query_runs_inline():
    ctx = locality_query.run(
        {"dc": "eu-central", "dataset": "orders-2026", "query": "count(*)"},
    )
    assert ctx.has_finished and ctx.has_succeeded
    assert ctx.output == {"dataset": "orders-2026", "rows": ["result(count(*))"]}


def test_tenant_report_runs_inline():
    ctx = tenant_report.run({"tenant_id": "acme"})
    assert ctx.has_finished and ctx.has_succeeded
    assert ctx.output == {"tenant": "acme", "report": "ok"}


def test_nightly_cleanup_runs_inline():
    ctx = nightly_cleanup.run()
    assert ctx.has_finished and ctx.has_succeeded
    assert ctx.output == {"cleaned": True}


def test_workflows_carry_compiled_expressions():
    assert [term["kind"] for term in locality_query.affinity] == [
        "match",
        "match",
        "match",
        "when",
    ]
    # The scoring stage speaks the same vocabulary: a dynamic-key prefer.
    assert [term["kind"] for term in locality_query.routing["terms"]] == ["prefer", "least"]
    assert locality_query.routing["terms"][0]["selector"] == {
        "kind": "label",
        "prefix": "cache.",
        "input": "dataset",
    }
    assert tenant_report.affinity == [
        {
            "kind": "match",
            "selector": "label:tenant",
            "op": "==",
            "value": {"$input": "tenant_id"},
        },
    ]
    assert nightly_cleanup.affinity[0]["op"] == "!="


def test_catalog_extraction_matches_decorators():
    """The AST extraction of the example source agrees with the runtime
    factories — the guarantee that server-side registration sees exactly
    what the decorator built."""
    from flux.catalogs import WorkflowCatalog

    infos = {i.name: i for i in WorkflowCatalog.create().parse(EXAMPLE.read_bytes())}
    assert infos["locality_query"].affinity == locality_query.affinity
    assert (infos["locality_query"].metadata or {})["routing"] == locality_query.routing
    assert infos["tenant_report"].affinity == tenant_report.affinity
    assert infos["nightly_cleanup"].affinity == nightly_cleanup.affinity
