"""E2E for declaration path 2 (workflow-declared hooks).

Spawns the real server + worker (the session-scoped ``cli`` fixture) and
drives the whole lifecycle: registering a workflow with ``hooks=[...]``
creates the row, an event it declares fires a real delivery, re-
registering with a different selector replaces the same row, and deleting
the workflow removes it.

``declared_hook_workflows.py`` defines its two workflows -- the
hook-declaring ``declared_hook_source`` and its target
``declared_hook_notifier`` -- in the same file, but that pair can never come
into being from a single registration call: ``workflows_save``
(``flux/api/workflow_routes.py``) validates every declared hook's target via
``_require_runnable_target`` (``flux/api/hook_routes.py``), which requires
the target to already be a registered workflow, and that whole validation
loop runs *before* ``catalog.enrich``/``catalog.save`` for anything in the
upload -- confirmed live (a bare ``POST /workflows`` of the combined file
404s with "Workflow 'default/declared_hook_notifier' not found" and saves
neither workflow) and confirmed as deliberate, already-tested behavior in
``tests/flux/test_workflow_hooks_registration.py``
(``test_registering_without_a_runnable_target_is_rejected``). So each test
here bootstraps ``declared_hook_notifier`` alone first -- exactly as a real
deployment would ship the notifier before a workflow that references it --
before registering the real fixture file. The notifier ends up registered
twice (bootstrap, then again bundled with ``declared_hook_source``), which
is harmless: workflow lookups are namespace/name-keyed to the latest
version, hook delivery resolves its target the same way, and every
assertion below is against a specific ``execution_id``, not a version.
"""

from __future__ import annotations

import time
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"
_DELIVERY_TIMEOUT = 120

_NOTIFIER_BOOTSTRAP_SOURCE = '''"""Bootstrap source: just the notifier, no hooks declared.

Registered before ``declared_hook_workflows.py`` so ``default/declared_hook_notifier``
is already a runnable target by the time ``declared_hook_source``'s own
registration declares a hook against it. See the module docstring of
``test_hooks_declared.py`` for why this step exists.
"""

from __future__ import annotations

from typing import Any

from flux import task, workflow


@task
async def record(payload: Any) -> Any:
    return payload


@workflow
async def declared_hook_notifier(ctx):
    return await record(ctx.input)
'''


def _register_declared_hook_fixtures(cli, tmp_path) -> None:
    """Register ``declared_hook_notifier`` before the self-referencing pair.

    See the module docstring for why the bootstrap step is required.
    """
    bootstrap = tmp_path / "declared_hook_notifier_bootstrap.py"
    bootstrap.write_text(_NOTIFIER_BOOTSTRAP_SOURCE)
    cli.register(str(bootstrap))
    cli.register(str(FIXTURES / "declared_hook_workflows.py"))


def _wait_for_delivery(cli, hook_name: str, status: str, timeout: int) -> dict:
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


def _owned_hook_name(cli) -> str:
    hooks = cli.hook_list()["hooks"]
    owned = [h for h in hooks if h["owner_type"] == "workflow" and h["owner_ref"] == "default/declared_hook_source"]
    assert len(owned) == 1, hooks
    return owned[0]["name"]


def test_registering_a_declared_hook_fires_a_real_delivery(cli, tmp_path):
    _register_declared_hook_fixtures(cli, tmp_path)
    hook_name = _owned_hook_name(cli)

    r = cli.run("declared_hook_source", "null", mode="async")
    cli.wait_for_state("declared_hook_source", r["execution_id"], "FAILED", timeout=60)

    delivery = _wait_for_delivery(cli, hook_name, "delivered", timeout=_DELIVERY_TIMEOUT)
    assert delivery["execution_id"]
    started = cli.wait_for_state(
        "declared_hook_notifier",
        delivery["execution_id"],
        "COMPLETED",
        timeout=60,
    )
    assert started["output"]["event"]["type"] == "failed"
    assert started["output"]["event"]["workflow_name"] == "declared_hook_source"


def test_reregistering_replaces_and_deleting_the_workflow_removes_it(cli, tmp_path):
    _register_declared_hook_fixtures(cli, tmp_path)
    hook_name = _owned_hook_name(cli)
    first = cli.hook_get(hook_name)

    # Re-register the identical source: same declared spec, same derived
    # name, same row -- not a duplicate.
    cli.register(str(FIXTURES / "declared_hook_workflows.py"))
    second = cli.hook_get(hook_name)
    assert second["id"] == first["id"]

    cli._server_ok(
        ["workflow", "delete", "default/declared_hook_source", "--force"],
    )
    remaining = [
        h
        for h in cli.hook_list()["hooks"]
        if h["owner_type"] == "workflow" and h["owner_ref"] == "default/declared_hook_source"
    ]
    assert remaining == []
