"""The catalog statically extracts require(...) affinity expressions (AST, no exec)."""

from __future__ import annotations

import pytest

from flux.catalogs import WorkflowCatalog


def _parse(source: bytes):
    return WorkflowCatalog.create().parse(source)


def test_require_expression_extracted_into_affinity():
    source = b"""
from flux import workflow
from flux.routing import require, optional, when, label, label_for, service, input


@workflow.with_options(
    affinity=require(
        service(input("model")),
        label_for("sku.", input("model")) == "true",
        optional(label("node") == input("node")),
        when(input("tier") == "dedicated", label("cap.dedicated") == "true"),
    ),
)
async def infer(ctx):
    return 1


@workflow.with_options(affinity={"gpu": "a100"})
async def legacy(ctx):
    return 2
"""
    infos = {i.name: i for i in _parse(source)}

    assert infos["infer"].affinity == [
        {
            "kind": "match",
            "selector": {"kind": "label", "prefix": "flux.service.", "input": "model"},
            "op": "==",
            "value": "true",
        },
        {
            "kind": "match",
            "selector": {"kind": "label", "prefix": "sku.", "input": "model"},
            "op": "==",
            "value": "true",
        },
        {
            "kind": "match",
            "selector": "label:node",
            "op": "==",
            "value": {"$input": "node"},
            "optional": True,
        },
        {
            "kind": "when",
            "if": {"input": "tier", "op": "==", "value": "dedicated"},
            "then": {
                "kind": "match",
                "selector": "label:cap.dedicated",
                "op": "==",
                "value": "true",
            },
        },
    ]
    # The dict form remains valid forever.
    assert infos["legacy"].affinity == {"gpu": "a100"}


def test_reversed_comparison_extracts_symmetrically():
    source = b"""
from flux import workflow
from flux.routing import require, label, input


@workflow.with_options(affinity=require("eu-west" == label("region")))
async def wf(ctx):
    return 1
"""
    [info] = _parse(source)
    assert info.affinity == [
        {"kind": "match", "selector": "label:region", "op": "==", "value": "eu-west"},
    ]


def test_static_service_name_extracts():
    source = b"""
from flux import workflow
from flux.routing import require, service


@workflow.with_options(affinity=require(service("inference")))
async def wf(ctx):
    return 1
"""
    [info] = _parse(source)
    assert info.affinity == [
        {
            "kind": "match",
            "selector": "label:flux.service.inference",
            "op": "==",
            "value": "true",
        },
    ]


@pytest.mark.parametrize(
    ("decorator", "reason"),
    [
        ('require(label("x") > "1")', "only == and !="),
        ('require(label("x") == variable)', "unsupported value expression"),
        ("require()", "at least one term"),
        ('require(metric("temp") == 1)', "must be label"),
        ('require(when(label("x") == "1", label("y") == "1"))', "must be input"),
        ('require(label_for(prefix, input("m")) == "true")', "literal prefix"),
        ('require(optional(label("x") == "1", label("y") == "1"))', "exactly one term"),
        ('require(label("x") == "1", junk="yes")', "no keyword arguments"),
        ("require(service(42))", "service"),
        ('require(service("Not_Valid"))', "lowercase letters"),
        ('require(label("x") == input(42))', "Invalid input"),
    ],
)
def test_unparseable_require_fails_registration_loudly(decorator, reason):
    source = f"""
from flux import workflow
from flux.routing import require, optional, when, label, label_for, service, metric, input

variable = "not-static"
prefix = "sku."


@workflow.with_options(affinity={decorator})
async def wf(ctx):
    return 1
""".encode()
    with pytest.raises(SyntaxError, match=reason):
        _parse(source)


def test_non_require_call_affinity_stays_none():
    # Anything that is neither a dict literal nor require(...) keeps the
    # legacy permissive behavior: no affinity extracted.
    source = b"""
from flux import workflow

labels = {"gpu": "a100"}


@workflow.with_options(affinity=labels)
async def wf(ctx):
    return 1
"""
    [info] = _parse(source)
    assert info.affinity is None
