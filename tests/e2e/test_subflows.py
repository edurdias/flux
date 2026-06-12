"""E2E tests — subflows and nested workflow calls via HTTP."""

from __future__ import annotations

import pytest

# Both tests run examples that call the live GitHub API from the worker.
# Unauthenticated requests are limited to 60/hour/IP, which makes them flaky
# on shared CI runners (and unreachable from some sandboxes), so they are
# excluded from CI via the network marker.
pytestmark = pytest.mark.network


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
