"""E2E tests — admin operations: health, workers, executions, roles, principals."""
from __future__ import annotations


def test_health(cli):
    h = cli.health()
    assert h["status"] == "healthy"


def test_worker_list(cli):
    workers = cli.worker_list()
    names = [w["name"] for w in workers]
    assert "e2e-worker" in names


def test_worker_show(cli):
    w = cli.worker_show("e2e-worker")
    assert w["name"] == "e2e-worker"
    assert "runtime" in w or "resources" in w


def test_execution_list_and_show(cli):
    cli.register("examples/hello_world.py")
    run_result = cli.run("hello_world", '"admin_test"')
    exec_id = run_result["execution_id"]
    listing = cli.execution_list()
    exec_ids = [
        e["execution_id"]
        for e in (listing.get("executions", listing) if isinstance(listing, dict) else listing)
    ]
    assert exec_id in exec_ids
    detail = cli.execution_show(exec_id)
    assert detail["execution_id"] == exec_id


def test_role_lifecycle(cli):
    cli.role_create("e2e_test_role", ["workflow:*:*:read"])
    roles = cli.role_list()
    assert any(r.get("name") == "e2e_test_role" for r in roles)
    r = cli.role_show("e2e_test_role")
    assert "workflow:*:*:read" in r.get("permissions", [])
    cli.role_delete("e2e_test_role")
    roles = cli.role_list()
    assert not any(r.get("name") == "e2e_test_role" for r in roles)


def test_role_clone(cli):
    cloned = cli.role_clone("viewer", "e2e_cloned_viewer")
    assert cloned.get("name") == "e2e_cloned_viewer"
    cli.role_delete("e2e_cloned_viewer")


def test_role_update(cli):
    cli.role_create("e2e_update_role", ["workflow:*:*:read"])
    cli.role_update("e2e_update_role", add=["schedule:*:read"])
    r = cli.role_show("e2e_update_role")
    assert "schedule:*:read" in r.get("permissions", [])
    cli.role_delete("e2e_update_role")


def test_principal_lifecycle(cli):
    cli.principal_create("e2e_sa", principal_type="service_account", roles=["viewer"])
    principals = cli.principal_list()
    assert any(p.get("subject") == "e2e_sa" for p in principals)
    p = cli.principal_show("e2e_sa")
    assert p["subject"] == "e2e_sa"
    cli.principal_delete("e2e_sa")


def test_principal_enable_disable(cli):
    cli.principal_create("e2e_toggle", principal_type="service_account")
    cli.principal_disable("e2e_toggle")
    p = cli.principal_show("e2e_toggle")
    assert p.get("enabled") is False
    cli.principal_enable("e2e_toggle")
    p = cli.principal_show("e2e_toggle")
    assert p.get("enabled") is True
    cli.principal_delete("e2e_toggle")


def test_api_key_lifecycle(cli):
    cli.principal_create("e2e_keytest", principal_type="service_account")
    key = cli.principal_create_key("e2e_keytest", "test-key")
    assert "key" in key or "api_key" in key or "token" in key
    keys = cli.principal_list_keys("e2e_keytest")
    assert len(keys) >= 1
    cli.principal_revoke_key("e2e_keytest", "test-key")
    cli.principal_delete("e2e_keytest")


def test_auth_status(cli):
    s = cli.auth_status()
    assert isinstance(s, dict)


def test_auth_permissions(cli):
    p = cli.auth_permissions()
    assert isinstance(p, (dict, list))


def test_schedule_delete(cli):
    cli.register("examples/hello_world.py")
    s = cli.schedule_create("hello_world", "e2e_delete_me", cron="0 0 * * *")
    sched_id = s["id"]
    cli.schedule_delete(sched_id)
    schedules = cli.schedule_list()
    assert not any(sc.get("id") == sched_id for sc in schedules)


def test_secrets_lifecycle(cli):
    cli.secrets_set("e2e_secret", "test_value")
    v = cli.secrets_get("e2e_secret")
    assert v.get("value") == "test_value" or "e2e_secret" in str(v)
    listing = cli.secrets_list()
    names = listing.get("secrets", listing) if isinstance(listing, dict) else listing
    assert "e2e_secret" in str(names)
    cli.secrets_remove("e2e_secret")
