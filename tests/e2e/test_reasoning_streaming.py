"""E2E: verify reasoning/thinking content is streamed as progress events.

Uses a tool-calling agent (which forces the non-stream LLM call path in
agent_loop.py) with reasoning_effort="high" so the model produces thinking
content. Captures the raw SSE stream during resume and asserts that at least
one ``{"type": "reasoning", "text": "..."}`` progress event is emitted.

Requires Ollama with a reasoning-capable model (qwen3:latest).
"""

from __future__ import annotations

import json

import httpx
import pytest


@pytest.mark.ollama
def test_reasoning_events_streamed_during_tool_calling(cli):
    """Resume a tool-calling agent and verify reasoning progress events appear in SSE."""
    # Register template
    cli.register("flux/agents/template.py")

    agent_name = "e2e_reasoning_stream_test"

    # Create agent with tools + reasoning
    r = httpx.post(
        f"{cli.server_url}/admin/agents",
        json={
            "name": agent_name,
            "model": "ollama/qwen3:latest",
            "system_prompt": "You are a file assistant. Think step by step. Use list_directory.",
            "tools": [{"directory": {"workspace": "/tmp"}}],
            "max_tool_calls": 2,
            "reasoning_effort": "high",
        },
        timeout=10,
    )
    assert r.status_code == 200, f"Failed to create agent: {r.text}"

    try:
        # Run workflow → PAUSED at turn 0
        result = cli.run("agents/agent_chat", f'{{"agent": "{agent_name}"}}', mode="async")
        exec_id = result["execution_id"]
        cli.wait_for_state("agents/agent_chat", exec_id, "PAUSED", timeout=30)

        # Resume via streaming endpoint and capture raw SSE
        resume_url = f"{cli.server_url}/workflows/agents/agent_chat/resume/{exec_id}/stream"
        reasoning_events: list[dict] = []
        tool_events: list[str] = []

        with httpx.Client(timeout=120) as client:
            with client.stream(
                "POST",
                resume_url,
                json={"message": "List files."},
            ) as resp:
                assert resp.status_code == 200
                buf: list[str] = []
                for line in resp.iter_lines():
                    if line.startswith("data:"):
                        buf.append(line[5:].strip())
                    elif line.strip() == "" and buf:
                        try:
                            payload = json.loads("\n".join(buf))
                        except json.JSONDecodeError:
                            buf.clear()
                            continue
                        buf.clear()
                        val = payload.get("value", {})
                        if isinstance(val, dict):
                            vtype = val.get("type", "")
                            if vtype == "reasoning":
                                reasoning_events.append(val)
                            elif vtype in ("tool_start", "tool_done"):
                                tool_events.append(vtype)
                        state = payload.get("state", "")
                        if state in ("PAUSED", "COMPLETED", "FAILED"):
                            break

        # Assertions
        assert len(reasoning_events) >= 1, (
            f"Expected at least 1 reasoning progress event; got {len(reasoning_events)}. "
            f"Tool events seen: {tool_events}"
        )
        for re_evt in reasoning_events:
            assert "text" in re_evt, f"Reasoning event missing 'text': {re_evt}"
            assert len(re_evt["text"]) > 0, "Reasoning text should not be empty"

        assert len(tool_events) >= 2, (
            f"Expected at least 2 tool events (start+done); got {tool_events}"
        )

    finally:
        httpx.delete(f"{cli.server_url}/admin/agents/{agent_name}", timeout=10)
