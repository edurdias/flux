"""E2E tests — pause, resume, and cancellation workflows."""
from __future__ import annotations

import time
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


def test_pause_and_resume(cli):
    cli.register("examples/pause.py")
    r = cli.run("pause_workflow", "null", mode="async")
    exec_id = r["execution_id"]
    s = cli.wait_for_state("pause_workflow", exec_id, "PAUSED", timeout=30)
    assert s["state"] == "PAUSED"
    cli.resume("pause_workflow", exec_id)
    s = cli.wait_for_state("pause_workflow", exec_id, "COMPLETED", timeout=30)
    assert s["state"] == "COMPLETED"


def test_multiple_pause_points(cli):
    cli.register("examples/multiple_pause_points.py")
    r = cli.run("multi_pause_workflow", "null", mode="async")
    exec_id = r["execution_id"]
    for _ in range(5):
        s = cli.wait_for_state("multi_pause_workflow", exec_id, "PAUSED", timeout=30)
        if s["state"] == "COMPLETED":
            break
        assert s["state"] == "PAUSED"
        cli.resume("multi_pause_workflow", exec_id)
        time.sleep(1)
    s = cli.wait_for_state("multi_pause_workflow", exec_id, "COMPLETED", timeout=30)
    assert s["state"] == "COMPLETED"


def test_dataframe_with_pause(cli):
    cli.register("examples/dataframe_with_pause.py")
    csv_path = str(FIXTURES / "sales_sample.csv")
    r = cli.run(
        "dataframe_with_pause",
        f'{{"file_path": "{csv_path}"}}',
        mode="async",
    )
    exec_id = r["execution_id"]
    s = cli.wait_for_state("dataframe_with_pause", exec_id, "PAUSED", timeout=30)
    assert s["state"] == "PAUSED"
    cli.resume("dataframe_with_pause", exec_id)
    s = cli.wait_for_state("dataframe_with_pause", exec_id, "COMPLETED", timeout=30)
    assert s["state"] == "COMPLETED"


def test_cancellation(cli):
    cli.register(str(FIXTURES / "cancellable_workflow.py"))
    r = cli.run("cancellable_e2e", "60", mode="async")
    exec_id = r["execution_id"]
    time.sleep(5)
    cli.cancel("cancellable_e2e", exec_id)
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        s = cli.status("cancellable_e2e", exec_id)
        if s["state"] in ("CANCELLED", "CANCELLING"):
            break
        time.sleep(2)
    assert s["state"] in ("CANCELLED", "CANCELLING")
