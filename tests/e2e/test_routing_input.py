"""Routing-only execution input against a real server and worker (issue #211).

The unit tests prove the pieces; this proves the path. A defect in this feature
found during review was invisible to unit tests precisely because it lived in
the ingress rather than the validator, so the assertions here run through the
CLI and a live worker.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from .conftest import FluxCLIError

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module", autouse=True)
def registered(cli):
    cli.register(str(FIXTURES / "routing_input_workflow.py"))


def test_routing_values_never_reach_the_worker(cli):
    """The guarantee, end to end: the workflow returns what it received, and
    the routing values are not in it."""
    result = cli.run(
        "routing_echo",
        '{"real": "payload"}',
        routing_input={"cohort": "canary"},
    )

    assert result["state"] == "COMPLETED"
    seen = result["output"]["seen_input"]
    assert seen == {"real": "payload"}
    assert "cohort" not in str(seen)


def test_routing_values_are_absent_from_the_execution_read(cli):
    """`workflow status` is the API a worker can call for itself — the worker
    built-in role holds execution:*:read."""
    result = cli.run(
        "routing_echo",
        '{"real": "payload"}',
        routing_input={"cohort": "canary"},
    )

    status = cli.status("routing_echo", result["execution_id"])

    assert "canary" not in str(status)
    assert "routing_input" not in str(status)


def test_an_execution_with_and_without_routing_values_look_alike(cli):
    """Indistinguishability is the actual claim, so compare the two directly
    rather than asserting on one of them."""
    with_values = cli.run("routing_echo", '{"real": "payload"}', routing_input={"cohort": "canary"})
    without = cli.run("routing_echo", '{"real": "payload"}')

    assert with_values["output"]["seen_input"] == without["output"]["seen_input"]

    keys_with = set(cli.status("routing_echo", with_values["execution_id"]))
    keys_without = set(cli.status("routing_echo", without["execution_id"]))
    assert keys_with == keys_without


def test_routing_values_steer_dispatch(cli):
    """They are invisible *and* effective — a value the worker cannot see still
    decides where the execution runs."""
    worker = cli.start_worker("cohort-canary", labels={"cohort": "canary"})
    try:
        result = cli.run(
            "routing_pinned",
            "null",
            routing_input={"cohort": "canary"},
            timeout=120,
        )

        assert result["state"] == "COMPLETED"
        assert result["output"]["worker"] == "cohort-canary"
        assert result["output"]["seen_input"] is None, "routing values must not become input"
    finally:
        cli.stop_worker(worker)


def test_invalid_routing_input_is_rejected_by_the_cli(cli):
    """Rejected, never dropped — a dropped directive routes somewhere the
    caller did not intend and reports success."""
    with pytest.raises(FluxCLIError) as excinfo:
        cli.run("routing_echo", "null", routing_input={"a.b": "1"})

    assert excinfo.value.returncode != 0
