"""E2E tests for outbound hooks.

Spawns a real server + worker (the session-scoped ``cli`` fixture), then
drives the whole path a hook takes in production: an engine event is
recorded, the outbox writes a delivery in the same transaction, the
scheduler tick drains it, and the target workflow runs with the redacted
envelope as its input.

Two properties are worth an e2e rather than a unit test, because both need
the scheduler, a worker and the catalog to be real:

* a delivery reaches ``delivered`` carrying the id of the execution it
  started, and that execution's input *is* the envelope;
* a hook subscribed to its own target's completion stops at the hop limit
  with a ``dead`` delivery instead of looping forever.

The drain rides the scheduler tick, so every wait here is measured in ticks,
not milliseconds — hence the generous timeouts. The tick is 30s by default;
``FLUX_SCHEDULING__POLL_INTERVAL=5`` in the environment is inherited by the
server the fixture spawns and makes the hop-chain case finish in seconds.
"""

from __future__ import annotations

import time
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"

# Four ticks of the scheduler at worst (the hop chain), plus the executions
# between them.
_CHAIN_TIMEOUT = 240
_DELIVERY_TIMEOUT = 120


def _wait_for_delivery(cli, hook_name: str, status: str, timeout: int) -> dict:
    """Poll ``flux hook deliveries`` until a row reaches ``status``."""
    deadline = time.monotonic() + timeout
    rows: list = []
    while time.monotonic() < deadline:
        rows = cli.hook_deliveries(hook_name)
        matching = [row for row in rows if row["status"] == status]
        if matching:
            return matching[0]
        time.sleep(2)
    raise TimeoutError(
        f"No '{status}' delivery for hook {hook_name} within {timeout}s; saw "
        f"{[(row['status'], row['attempts'], row['last_error']) for row in rows]}",
    )


def _wait_for_deliveries(cli, hook_name: str, count: int, timeout: int) -> list:
    """Poll until the hook has at least ``count`` delivery rows."""
    deadline = time.monotonic() + timeout
    rows: list = []
    while time.monotonic() < deadline:
        rows = cli.hook_deliveries(hook_name)
        if len(rows) >= count:
            return rows
        time.sleep(2)
    raise TimeoutError(
        f"Hook {hook_name} had {len(rows)} delivery(ies), expected {count}, within {timeout}s: "
        f"{[(row['status'], row['payload'].get('hop')) for row in rows]}",
    )


def _pending_approval(cli, exec_id: str, timeout: int = 60) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        out = cli._server_json(["execution", "approvals", "--execution", exec_id, "--json"])
        pending = [row for row in out.get("approvals", []) if row.get("status") == "pending"]
        if pending:
            return pending[0]
        time.sleep(1)
    raise TimeoutError(f"No pending approval appeared for execution {exec_id} within {timeout}s")


def test_hook_on_an_approval_gate_starts_the_target_with_the_envelope(cli):
    cli.register(str(FIXTURES / "hook_workflows.py"))

    hook = cli.hook_create(
        "e2e-approval-hook",
        selectors=["task:*:*:hook_gate:awaiting_approval"],
        workflow_ref="default/hook_recorder",
    )
    assert hook["selectors"] == ["task:*:*:hook_gate:awaiting_approval"]
    assert hook["workflow_ref"] == "default/hook_recorder"
    assert hook["enabled"] is True

    exec_id = None
    try:
        r = cli.run("hook_gate_e2e", '"value-1"', mode="async")
        exec_id = r["execution_id"]
        cli.wait_for_state("hook_gate_e2e", exec_id, "PAUSED", timeout=60)

        delivery = _wait_for_delivery(
            cli,
            "e2e-approval-hook",
            "delivered",
            timeout=_DELIVERY_TIMEOUT,
        )
        assert delivery["execution_id"], delivery
        assert delivery["attempts"] == 1
        assert delivery["last_error"] is None

        envelope = delivery["payload"]
        assert envelope["hook"] == "e2e-approval-hook"
        assert envelope["selector"] == "task:*:*:hook_gate:awaiting_approval"
        assert envelope["hop"] == 0
        assert envelope["delivery_id"] == delivery["id"]
        assert envelope["event"]["domain"] == "task"
        assert envelope["event"]["type"] == "awaiting_approval"
        assert envelope["event"]["task_name"] == "hook_gate"
        assert envelope["event"]["execution_id"] == exec_id
        assert envelope["event"]["workflow_name"] == "hook_gate_e2e"

        # The delivery is the audit link: the execution it names really ran,
        # and what it ran on is the envelope.
        started = cli.wait_for_state(
            "hook_recorder",
            delivery["execution_id"],
            "COMPLETED",
            timeout=60,
        )
        assert started["input"] == envelope
        assert started["output"] == envelope
    finally:
        if exec_id:
            # Leave nothing paused behind for later tests sharing the server.
            pending = _pending_approval(cli, exec_id)
            cli._server_ok(["execution", "approve", exec_id, pending["task_call_id"]])
            cli.wait_for_state("hook_gate_e2e", exec_id, "COMPLETED", timeout=60)
        cli.hook_delete("e2e-approval-hook")


def test_a_self_targeting_hook_stops_at_the_hop_limit(cli):
    """`execution:*:*:completed` targeting a workflow that itself completes is
    a fork bomb; the hop guard is what makes it terminate. The selector is
    narrowed to the loop workflow so the chain is this test's alone."""
    cli.register(str(FIXTURES / "hook_workflows.py"))

    cli.hook_create(
        "e2e-loop-hook",
        selectors=["execution:default:hook_loop:completed"],
        workflow_ref="default/hook_loop",
    )

    try:
        # Seeded by an ordinary run: its input carries no hop, so the first
        # delivery is first-generation (hop 0).
        r = cli.run("hook_loop", '{"seed": true}', mode="async")
        cli.wait_for_state("hook_loop", r["execution_id"], "COMPLETED", timeout=60)

        dead = _wait_for_delivery(cli, "e2e-loop-hook", "dead", timeout=_CHAIN_TIMEOUT)
        assert "hop limit" in (dead["last_error"] or "")
        assert dead["execution_id"] is None, "a dead-lettered delivery started nothing"

        # hop_limit is 3: hops 0, 1 and 2 fire, and the fourth link dies.
        # There is no fifth: the dead row started no execution, so no further
        # completion event exists for this hook to match.
        rows = _wait_for_deliveries(cli, "e2e-loop-hook", 4, timeout=60)
        assert len(rows) == 4, rows
        assert sorted(row["payload"]["hop"] for row in rows) == [0, 1, 2, 3]
        assert dead["payload"]["hop"] == 3
        assert sorted(row["status"] for row in rows) == [
            "dead",
            "delivered",
            "delivered",
            "delivered",
        ]
    finally:
        cli.hook_delete("e2e-loop-hook")


def test_hook_test_fire_starts_the_target_without_a_delivery_row(cli):
    """`flux hook test` is the pre-flight: it starts the target with a
    synthetic envelope so a broken target or principal surfaces before an
    incident does — and writes no delivery row, since nothing happened."""
    cli.register(str(FIXTURES / "hook_workflows.py"))

    cli.hook_create(
        "e2e-test-fire-hook",
        selectors=["execution:default:never_fires:failed"],
        workflow_ref="default/hook_recorder",
    )

    try:
        result = cli.hook_test("e2e-test-fire-hook")
        assert result["hook"] == "e2e-test-fire-hook"
        assert result["envelope"]["hop"] == 0
        assert result["envelope"]["event"]["workflow_name"] == "never_fires"

        started = cli.wait_for_state(
            "hook_recorder",
            result["execution_id"],
            "COMPLETED",
            timeout=60,
        )
        assert started["output"] == result["envelope"]

        assert cli.hook_deliveries("e2e-test-fire-hook") == []
        assert any(h["name"] == "e2e-test-fire-hook" for h in cli.hook_list()["hooks"])
    finally:
        cli.hook_delete("e2e-test-fire-hook")
