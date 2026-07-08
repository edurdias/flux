"""The catalog statically extracts routing policies (AST, no exec)."""

from __future__ import annotations

import pytest

from flux.catalogs import WorkflowCatalog


def _parse(source: bytes):
    return WorkflowCatalog.create().parse(source)


def test_routing_policy_extracted_into_metadata(tmp_path, monkeypatch):
    source = b"""
from flux import workflow
from flux.routing import score, prefer, least, most, sticky, input


@workflow.with_options(
    routing=score(
        prefer("label:tier", "==", input("tier"), weight=10),
        prefer("label:region", "!=", "us-east", weight=4),
        least("metric:queue", weight=2),
        most("resource:memory_available"),
        sticky(weight=3),
        least("load"),
    ),
)
async def routed(ctx):
    return 1


@workflow
async def plain(ctx):
    return 2
"""
    infos = {i.name: i for i in _parse(source)}

    routing = (infos["routed"].metadata or {}).get("routing")
    assert routing is not None
    kinds = [t["kind"] for t in routing["terms"]]
    assert kinds == ["prefer", "prefer", "least", "most", "sticky", "least"]
    assert routing["terms"][0]["value"] == {"$input": "tier"}
    assert routing["terms"][1]["value"] == "us-east"
    assert (infos["plain"].metadata or {}).get("routing") is None


def test_namespaced_dsl_calls_are_extracted(tmp_path):
    source = b"""
from flux import workflow
import flux.routing as routing


@workflow.with_options(routing=routing.score(routing.least("load", weight=2)))
async def routed(ctx):
    return 1
"""
    (info,) = _parse(source)
    assert (info.metadata or {}).get("routing") == {
        "terms": [{"kind": "least", "selector": "load", "weight": 2.0}],
    }


@pytest.mark.parametrize(
    "decorator, message",
    [
        (b"routing=score(least(WEIGHTS))", "statically declarable"),
        (b"routing=my_policy", "expected a score"),
        (b"routing=score(pick_random())", "expected prefer"),
        (b'routing=score(least("cpu"))', "Invalid routing term"),
        (b'routing=score(prefer("label:x", "~=", "y"))', "Invalid routing term"),
        (b"routing=score()", "Invalid routing policy"),
        (b'routing=score(least("load", weight=w))', "statically declarable"),
    ],
)
def test_unparseable_or_invalid_routing_raises(decorator, message):
    source = (
        b"""
from flux import workflow
from flux.routing import score, prefer, least


@workflow.with_options(%s)
async def routed(ctx):
    return 1
"""
        % decorator
    )
    with pytest.raises(SyntaxError, match=message):
        _parse(source)
