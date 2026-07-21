"""E2E test for server-side worker metadata (issue #138).

An execution gated on require(meta(...)) parks while no worker carries the
metadata, then dispatches — without the worker reconnecting — once an
operator sets the value through `flux worker metadata set`. Also pins the
CLI read-back surface (show/unset/clear) and the GET /workers exposure.

The session's shared e2e-worker never matches the gate (fail-closed require
against a worker with no metadata), so this module is insulated from it.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"

pytestmark = pytest.mark.e2e


def test_metadata_gates_dispatch_and_hot_updates(cli):
    worker = cli.start_worker("meta-gated-worker")
    try:
        cli.register(str(FIXTURES / "metadata_workflow.py"))
        r = cli.run("metadata_gated", "{}", mode="async", timeout=30)
        exec_id = r["execution_id"]

        # No worker carries the metadata: the execution must park, not fail.
        time.sleep(3)
        s = cli.status("metadata_gated", exec_id)
        assert s["state"] not in ("COMPLETED", "FAILED"), s

        # Operator flips the flag; the running worker must pick the execution
        # up without re-registering or reconnecting.
        result = cli.worker_metadata_set("meta-gated-worker", "dispatch.allowed=true")
        assert result["metadata"] == {"dispatch.allowed": "true"}

        final = cli.wait_for_state("metadata_gated", exec_id, "COMPLETED", timeout=30)
        assert final.get("current_worker") == "meta-gated-worker"
        assert final["output"] == {"served": True}

        # Read-back: CLI show and the fleet listing both surface the value.
        shown = cli.worker_metadata_show("meta-gated-worker")
        assert shown["metadata"] == {"dispatch.allowed": "true"}
        listed = {w["name"]: w for w in cli.worker_list()}
        assert listed["meta-gated-worker"]["metadata"] == {"dispatch.allowed": "true"}

        # Numeric values merge in as floats; unset/clear round-trip.
        merged = cli.worker_metadata_set("meta-gated-worker", "weight=5")
        assert merged["metadata"] == {"dispatch.allowed": "true", "weight": 5.0}
        after_unset = cli.worker_metadata_unset("meta-gated-worker", "weight")
        assert after_unset["metadata"] == {"dispatch.allowed": "true"}
        assert cli.worker_metadata_clear("meta-gated-worker")["metadata"] == {}
    finally:
        cli.stop_worker(worker)
