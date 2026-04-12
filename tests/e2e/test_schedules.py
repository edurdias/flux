"""E2E tests — schedule CRUD with namespaces."""
from __future__ import annotations


def test_schedule_create_qualified_ref(cli):
    cli.register("examples/namespaces/billing_invoice.py")
    s = cli.schedule_create("billing/invoice", "e2e_daily", cron="0 0 * * *")
    assert s["workflow_namespace"] == "billing"
    assert s["workflow_name"] == "invoice"


def test_schedule_create_bare_name(cli):
    cli.register("examples/hello_world.py")
    s = cli.schedule_create("hello_world", "e2e_hourly", cron="0 * * * *")
    assert s["workflow_namespace"] == "default"
    assert s["workflow_name"] == "hello_world"


def test_schedule_list_shows_namespace(cli):
    schedules = cli.schedule_list()
    has_billing = any(s.get("workflow_namespace") == "billing" for s in schedules)
    assert has_billing


def test_schedule_pause_resume(cli):
    cli.register("examples/hello_world.py")
    s = cli.schedule_create("hello_world", "e2e_pause_test", cron="0 6 * * *")
    sched_id = s["id"]
    cli.schedule_pause(sched_id)
    s = cli.schedule_show(sched_id)
    assert s["status"] == "PAUSED" or s["status"] == "paused"
    cli.schedule_resume(sched_id)
    s = cli.schedule_show(sched_id)
    assert s["status"] in ("ACTIVE", "active")


def test_schedule_show_history(cli):
    schedules = cli.schedule_list()
    sched_id = schedules[0]["id"]
    s = cli.schedule_show(sched_id)
    assert "workflow_name" in s
    h = cli.schedule_history(sched_id)
    assert isinstance(h, (dict, list))
