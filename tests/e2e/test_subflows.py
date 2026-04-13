"""E2E tests — subflows and nested workflow calls via HTTP."""
from __future__ import annotations


def test_subflows_github_stars(cli):
    cli.register("examples/subflows.py")
    r = cli.run("subflows", '["python/cpython","microsoft/vscode"]', timeout=60)
    assert r["state"] == "COMPLETED"
    assert isinstance(r["output"], dict)
    assert len(r["output"]) == 2


def test_github_stars_single(cli):
    cli.register("examples/github_stars.py")
    r = cli.run("github_stars", '["python/cpython"]', timeout=30)
    assert r["state"] == "COMPLETED"
