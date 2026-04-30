"""E2E: verify the worker resolves agent config via server API (no Ollama needed).

Creates an agent via POST /admin/agents (auto-publishes config), runs the real
agent_chat template, verifies PAUSED at turn 0 (proving get_config succeeded
on the worker via RemoteConfigManager). No LLM provider needed -- workflow
pauses before any LLM call.
"""

from __future__ import annotations

import httpx


def test_agent_config_resolved_via_server_api(cli):
    """Create agent via admin API, run agent_chat, verify PAUSED (not FAILED)."""
    cli.register("flux/agents/template.py")

    agent_name = "e2e_config_resolution_test"

    r = httpx.post(
        f"{cli.server_url}/admin/agents",
        json={
            "name": agent_name,
            "model": "ollama/qwen3:latest",
            "system_prompt": "Test agent for config resolution.",
        },
        timeout=10,
    )
    assert r.status_code == 200, f"Failed to create agent: {r.text}"

    try:
        result = cli.run("agents/agent_chat", f'{{"agent": "{agent_name}"}}', mode="async")
        exec_id = result["execution_id"]

        final = cli.wait_for_state("agents/agent_chat", exec_id, "PAUSED", timeout=30)
        assert final["state"] == "PAUSED", (
            f"Expected PAUSED (config resolved); got {final['state']}. "
            f"If FAILED, RemoteConfigManager may not be working."
        )
    finally:
        httpx.delete(f"{cli.server_url}/admin/agents/{agent_name}", timeout=10)
