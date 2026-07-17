"""The catalog statically extracts routing policies (AST, no exec)."""

from __future__ import annotations

import pytest

from flux.catalogs import WorkflowCatalog


def _parse(source: bytes):
    return WorkflowCatalog.create().parse(source)


def test_routing_policy_extracted_into_metadata():
    source = b"""
from flux import workflow
from flux.routing import score, prefer, least, most, sticky, label, metric, resource, load, input


@workflow.with_options(
    routing=score(
        prefer(label("tier") == input("tier"), weight=10),
        prefer(label("region") != "us-east", weight=4),
        prefer(metric("temp") < 60, weight=2),
        least(metric("queue"), weight=2),
        most(resource("memory_available")),
        sticky(weight=3),
        least(load()),
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
    assert kinds == ["prefer", "prefer", "prefer", "least", "most", "sticky", "least"]
    assert routing["terms"][0] == {
        "kind": "prefer",
        "selector": "label:tier",
        "op": "==",
        "value": {"$input": "tier"},
        "weight": 10.0,
    }
    assert routing["terms"][1]["op"] == "!="
    assert routing["terms"][2] == {
        "kind": "prefer",
        "selector": "metric:temp",
        "op": "<",
        "value": 60,
        "weight": 2.0,
    }
    assert routing["terms"][3]["selector"] == "metric:queue"
    assert routing["terms"][4]["selector"] == "resource:memory_available"
    assert routing["terms"][6]["selector"] == "load"
    assert (infos["plain"].metadata or {}).get("routing") is None


def test_reversed_comparison_is_flipped():
    source = b"""
from flux import workflow
from flux.routing import score, prefer, metric


@workflow.with_options(routing=score(prefer(60 > metric("temp"), weight=2)))
async def routed(ctx):
    return 1
"""
    (info,) = _parse(source)
    (term,) = (info.metadata or {})["routing"]["terms"]
    assert term["selector"] == "metric:temp"
    assert term["op"] == "<"
    assert term["value"] == 60


def test_namespaced_dsl_calls_are_extracted():
    source = b"""
from flux import workflow
import flux.routing as routing


@workflow.with_options(routing=routing.score(routing.least(routing.load(), weight=2)))
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
        (b"routing=score(least(load(), weight=WEIGHTS))", "statically declarable"),
        (b"routing=my_policy", "expected a score"),
        (b"routing=score(pick_random())", "expected prefer"),
        (b"routing=score(least(cpu()))", "statically declarable"),
        (b'routing=score(least("load"))', "statically declarable"),
        (b'routing=score(prefer(label("x")))', "statically declarable"),
        (b'routing=score(prefer(label("x") == label("y")))', "statically declarable"),
        (b'routing=score(prefer("x" == "y"))', "statically declarable"),
        (b'routing=score(least(resource("gpu_flops")))', "Invalid routing selector"),
        (b"routing=score()", "Invalid routing policy"),
        (b"routing=score(sticky(1))", "statically declarable"),
    ],
)
def test_unparseable_or_invalid_routing_raises(decorator, message):
    source = (
        b"""
from flux import workflow
from flux.routing import score, prefer, least, most, sticky, label, metric, resource, load


@workflow.with_options(%s)
async def routed(ctx):
    return 1
"""
        % decorator
    )
    with pytest.raises(SyntaxError, match=message):
        _parse(source)


def test_dynamic_vocabulary_extracted_in_score():
    source = b"""
from flux import workflow
from flux.routing import score, prefer, least, when, load, label_for, service, metric, input


@workflow.with_options(
    routing=score(
        prefer(label_for("cache.", input("dataset")) == "true", weight=5),
        prefer(service(input("model")), weight=2),
        when(input("latency_sensitive") == "true", least(metric("lag"), weight=10)),
        least(load()),
    ),
)
async def routed(ctx):
    return 1
"""
    [info] = _parse(source)
    terms = (info.metadata or {})["routing"]["terms"]
    assert terms[0] == {
        "kind": "prefer",
        "selector": {"kind": "label", "prefix": "cache.", "input": "dataset"},
        "op": "==",
        "value": "true",
        "weight": 5.0,
    }
    assert terms[1]["selector"] == {"kind": "label", "prefix": "flux.service.", "input": "model"}
    assert terms[2] == {
        "kind": "when",
        "if": {"input": "latency_sensitive", "op": "==", "value": "true"},
        "then": {"kind": "least", "selector": "metric:lag", "weight": 10.0},
    }


@pytest.mark.parametrize(
    ("term", "reason"),
    [
        ('least(label_for("c.", input("d")))', "no ordering"),
        ('when(label("x") == "1", least(load()))', "must be input"),
        ('when(input("t") == "1", label("y") == "1")', "expected prefer"),
        ('when(input("t") == "1", when(input("u") == "1", least(load())))', "when"),
        ('prefer(label_for(prefix, input("d")) == "true")', "literal prefix"),
        ("prefer(service(42))", "service"),
    ],
)
def test_unparseable_dynamic_score_terms_fail_loudly(term, reason):
    source = f"""
from flux import workflow
from flux.routing import score, prefer, least, when, load, label, label_for, service, input

prefix = "c."


@workflow.with_options(routing=score({term}))
async def wf(ctx):
    return 1
""".encode()
    with pytest.raises(SyntaxError, match=reason):
        _parse(source)
