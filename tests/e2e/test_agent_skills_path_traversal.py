"""E2E: agent_chat must reject skills_dir bundles that escape the temp dir.

Exercises the worker-side path-traversal validation added for the security
review (Vuln 1). Creates agents via POST /admin/agents with both safe and
malicious skills_dir JSON payloads, runs the real agent_chat template against
the live worker, and verifies the workflow state.

The shared E2E session disables auth, so this file does not exercise the
companion API-gate (workflow:*:*:register requirement for inline skills_dir
bundles). That path is covered by the route-level integration tests in
``tests/security/test_server_auth_routes.py`` which run the real FastAPI app
through TestClient with mocked auth.

Workflow registration is module-scoped (one registration shared by all three
tests) to dodge a pre-existing bug in Flux's worker module cache: after
``test_agent_harness_server_modes`` registers a stub ``agents/agent_chat`` at
some version N then deletes it, the worker keeps the compiled stub cached
under key ``agents:agent_chat:N``. Per-test re-registration would bump the
version on each call and eventually land back on the cached version, causing
the test to silently run the stub instead of the real template.
"""

from __future__ import annotations

import json

import httpx
import pytest


@pytest.fixture(scope="module", autouse=True)
def _register_agent_chat(cli):
    """Register the real agents/agent_chat exactly once for this module."""
    cli.register("flux/agents/template.py")
    yield


def _create_agent(cli, name: str, skills_dir: str) -> httpx.Response:
    return httpx.post(
        f"{cli.server_url}/admin/agents",
        json={
            "name": name,
            "model": "ollama/qwen3:latest",
            "system_prompt": "skills bundle test agent",
            "skills_dir": skills_dir,
        },
        timeout=10,
    )


def _delete_agent(cli, name: str) -> None:
    httpx.delete(f"{cli.server_url}/admin/agents/{name}", timeout=10)


def test_skills_dir_bundle_with_dotdot_path_fails_workflow(cli):
    """Malicious bundle with a `..` segment must fail the workflow at materialize-time."""
    name = "e2e_skills_dotdot"
    bundle = json.dumps({"a": {"../../../../tmp/flux_pwn_dotdot.txt": "rooted"}})

    r = _create_agent(cli, name, bundle)
    assert r.status_code == 200, f"Failed to create agent: {r.text}"

    try:
        result = cli.run("agents/agent_chat", f'{{"agent": "{name}"}}', mode="async")
        exec_id = result["execution_id"]

        final = cli.wait_for_state("agents/agent_chat", exec_id, "FAILED", timeout=30)
        assert (
            final["state"] == "FAILED"
        ), f"Expected FAILED (path-traversal rejected); got {final['state']}"

        detailed = cli.execution_show(exec_id, detailed=True)
        rendered = json.dumps(detailed)
        assert (
            "unsafe file path" in rendered or "escapes bundle root" in rendered
        ), f"Expected path-traversal error in execution events; got: {rendered[:1000]}"
    finally:
        _delete_agent(cli, name)


def test_skills_dir_bundle_with_absolute_path_fails_workflow(cli):
    """Malicious bundle with an absolute path must fail the workflow at materialize-time."""
    name = "e2e_skills_absolute"
    bundle = json.dumps({"a": {"/tmp/flux_pwn_absolute.txt": "rooted"}})

    r = _create_agent(cli, name, bundle)
    assert r.status_code == 200, f"Failed to create agent: {r.text}"

    try:
        result = cli.run("agents/agent_chat", f'{{"agent": "{name}"}}', mode="async")
        exec_id = result["execution_id"]

        final = cli.wait_for_state("agents/agent_chat", exec_id, "FAILED", timeout=30)
        assert final["state"] == "FAILED"

        detailed = cli.execution_show(exec_id, detailed=True)
        rendered = json.dumps(detailed)
        assert (
            "unsafe file path" in rendered
        ), f"Expected 'unsafe file path' in execution events; got: {rendered[:1000]}"
    finally:
        _delete_agent(cli, name)


def test_skills_dir_bundle_with_safe_paths_succeeds(cli):
    """A well-formed skills bundle must pause at turn 0 (no LLM call) like a normal agent."""
    name = "e2e_skills_safe"
    bundle = json.dumps(
        {
            "greeter": {
                "greeter/SKILL.md": "---\nname: greeter\ndescription: greets people\n---\nSay hi.\n",
            },
        },
    )

    r = _create_agent(cli, name, bundle)
    assert r.status_code == 200, f"Failed to create agent: {r.text}"

    try:
        result = cli.run("agents/agent_chat", f'{{"agent": "{name}"}}', mode="async")
        exec_id = result["execution_id"]

        final = cli.wait_for_state("agents/agent_chat", exec_id, "PAUSED", timeout=30)
        assert (
            final["state"] == "PAUSED"
        ), f"Expected PAUSED (safe bundle materialized); got {final['state']}"
    finally:
        _delete_agent(cli, name)
