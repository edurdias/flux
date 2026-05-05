"""E2E tests — workflow namespace isolation."""

from __future__ import annotations

from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


def test_namespace_registration(cli):
    cli.register("examples/namespaces/billing_invoice.py")
    wf = cli.show("billing/invoice")
    assert wf["namespace"] == "billing"
    assert wf["name"] == "invoice"


def test_cross_namespace_isolation(cli):
    cli.register(str(FIXTURES / "billing_process.py"))
    cli.register(str(FIXTURES / "analytics_process.py"))
    r_b = cli.run("billing/process", "null")
    r_a = cli.run("analytics/process", "null")
    assert r_b["workflow_namespace"] == "billing"
    assert r_a["workflow_namespace"] == "analytics"
    assert r_b["workflow_id"] != r_a["workflow_id"]
    assert r_b["output"]["namespace"] == "billing"
    assert r_a["output"]["namespace"] == "analytics"


def test_list_namespaces(cli):
    cli.register("examples/namespaces/billing_invoice.py")
    cli.register(str(FIXTURES / "analytics_process.py"))
    namespaces = cli.list_namespaces()
    ns_names = {n["namespace"] for n in namespaces}
    assert "billing" in ns_names
    assert "default" in ns_names


def test_namespace_filter(cli):
    cli.register("examples/namespaces/billing_invoice.py")
    billing = cli.list_workflows(namespace="billing")
    assert all(w["namespace"] == "billing" for w in billing)
    assert len(billing) >= 1


def test_delete_scoped_by_namespace(cli):
    cli.register(str(FIXTURES / "billing_process.py"))
    cli.register(str(FIXTURES / "analytics_process.py"))
    cli.delete("analytics/process")
    remaining = cli.list_workflows()
    names = {f"{w['namespace']}/{w['name']}" for w in remaining}
    assert "analytics/process" not in names
    assert "billing/process" in names


def test_execution_history_scoped(cli):
    cli.register(str(FIXTURES / "billing_process.py"))
    cli.run("billing/process", "null")
    result = cli.execution_list(namespace="billing")
    executions = result.get("executions", result) if isinstance(result, dict) else result
    for ex in executions:
        assert ex.get("workflow_namespace") == "billing"
