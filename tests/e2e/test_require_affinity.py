"""E2E tests for require(...) affinity expressions: per-execution routing.

One registered workflow, three behaviors driven purely by execution input:
region-matched dispatch, fail-fast on unresolved input, and a when() gate
that parks until a capable worker appears. The session's unlabeled
e2e-worker never matches a require term (fail-closed), so these tests are
insulated from it.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"

pytestmark = pytest.mark.e2e


def test_require_routes_by_execution_input(cli):
    """The same registration serves differently-routed requests."""
    eu = cli.start_worker("req-eu-worker", labels={"region": "eu-west"})
    us = cli.start_worker("req-us-worker", labels={"region": "us-east"})
    try:
        cli.register(str(FIXTURES / "require_workflow.py"))

        r = cli.run("require_task", '{"region": "eu-west"}')
        assert r["state"] == "COMPLETED"
        assert r.get("current_worker") == "req-eu-worker"
        assert r["output"] == {"served_region": "eu-west"}

        r = cli.run("require_task", '{"region": "us-east"}')
        assert r["state"] == "COMPLETED"
        assert r.get("current_worker") == "req-us-worker"
    finally:
        cli.stop_worker(eu)
        cli.stop_worker(us)


def test_require_unresolved_input_fails_fast(cli):
    """Missing required input is a named dispatch error, not a silent queue."""
    worker = cli.start_worker("req-diag-worker", labels={"region": "eu-west"})
    try:
        cli.register(str(FIXTURES / "require_workflow.py"))
        r = cli.run("require_task", '{"other": 1}', mode="async", timeout=30)
        final = cli.wait_for_state("require_task", r["execution_id"], "FAILED", timeout=30)
        assert "region" in str(final)
    finally:
        cli.stop_worker(worker)


def test_require_when_gate_waits_for_capable_worker(cli):
    """A when()-gated execution parks until a worker satisfying the gated
    term connects, then dispatches to it."""
    plain = cli.start_worker("req-plain-worker", labels={"region": "eu-west"})
    try:
        cli.register(str(FIXTURES / "require_workflow.py"))
        r = cli.run(
            "require_task",
            '{"region": "eu-west", "tier": "dedicated"}',
            mode="async",
            timeout=30,
        )
        exec_id = r["execution_id"]

        time.sleep(3)
        s = cli.status("require_task", exec_id)
        assert s["state"] not in ("COMPLETED", "FAILED"), s

        dedicated = cli.start_worker(
            "req-dedicated-worker",
            labels={"region": "eu-west", "cap.dedicated": "true"},
        )
        try:
            final = cli.wait_for_state("require_task", exec_id, "COMPLETED", timeout=30)
            assert final.get("current_worker") == "req-dedicated-worker"
        finally:
            cli.stop_worker(dedicated)
    finally:
        cli.stop_worker(plain)
