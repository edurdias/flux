"""End-to-end tests for agent harness Web and API serving modes.

The tests boot an ``AgentProcess`` in a background asyncio task, talk to its
HTTP endpoints, and verify the handshake with the real Flux server run by the
session-scoped ``cli`` fixture.

We rely only on getting a ``session_id`` back over SSE (which is emitted by
the Flux server as soon as the execution starts, before any LLM call is made),
so these tests do not require Ollama, API keys, or any model provider.
"""

from __future__ import annotations

import asyncio
import socket

import httpx
import pytest

from flux.agents.process import AgentProcess

AGENT_WORKFLOW_SRC = '''\
"""Minimal agent_chat workflow for E2E harness tests.

Registered in the ``agents`` namespace so ``FluxClient.start_agent`` can
reach it at ``/workflows/agents/agent_chat/...``. This stub pauses with a
``chat_response`` output so the test can observe the SSE handshake and
session_id propagation without needing a real LLM provider.
"""
from __future__ import annotations

from typing import Any

from flux.domain.execution_context import ExecutionContext
from flux.tasks.pause import pause
from flux.workflow import workflow

from flux.agents.types import ChatResponseOutput


@workflow.with_options(namespace="agents")
async def agent_chat(ctx: ExecutionContext[dict[str, Any]]):
    turn = 0
    response = None
    while True:
        output = ChatResponseOutput(content=response, turn=turn).model_dump()
        next_input = await pause(f"turn_{turn}", output=output)
        response = f"echo: {next_input.get('message', '')}"
        turn += 1
'''


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _wait_ready(url: str, timeout: float = 15.0) -> None:
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    last_err: Exception | None = None
    while loop.time() < deadline:
        try:
            async with httpx.AsyncClient(timeout=1.0) as client:
                resp = await client.get(url)
                if resp.status_code < 500:
                    return
        except Exception as exc:  # noqa: BLE001
            last_err = exc
        await asyncio.sleep(0.1)
    raise TimeoutError(f"Server at {url} did not become ready within {timeout}s: {last_err}")


@pytest.fixture(scope="module")
def agent_harness_env(cli, tmp_path_factory):
    """Register a stub ``agents/agent_chat`` workflow for the harness tests.

    The stub pauses on each turn with a ``chat_response`` output so the test
    can verify SSE event flow through ``AgentProcess`` without needing any
    LLM provider. It does not consume an agent config entry.

    **Ordering note:** This file (``test_agent_harness_server_modes``) runs
    before ``test_agent_harness_ollama`` alphabetically. The teardown below
    deletes the stub workflow so the Ollama tests can re-register the real
    ``agent_chat`` template without version conflicts.
    """
    tmp = tmp_path_factory.mktemp("agent_harness")
    wf_file = tmp / "agent_chat_ns_agents.py"
    wf_file.write_text(AGENT_WORKFLOW_SRC)

    cli.register(str(wf_file))

    yield {
        "agent_name": "e2e_harness_agent",
        "server_url": cli.server_url,
        "token": "e2e-bearer",
    }

    try:
        cli.delete("agents/agent_chat")
    except Exception:  # noqa: BLE001
        pass


async def _cancel_and_wait(task: asyncio.Task) -> None:
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):  # noqa: BLE001
        pass


@pytest.mark.asyncio
async def test_api_mode_health_and_console_surface(agent_harness_env):
    """api mode serves `/health` plus the console's `/console/*` routes --
    the same surface web mode serves, with a per-request Bearer. The
    single-agent `/chat`, `/elicitation`, `/approval` and `/session` routes
    were removed with the console."""
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"

    process = AgentProcess(
        agent_name=agent_harness_env["agent_name"],
        server_url=agent_harness_env["server_url"],
        mode="api",
        port=port,
        token=None,
    )
    server_task = asyncio.create_task(process.run())

    try:
        await _wait_ready(f"{base_url}/health")
        bearer = agent_harness_env["token"]

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(f"{base_url}/health")
            assert resp.status_code == 200
            assert resp.json() == {"status": "ok"}

            for path in ("/chat", "/session/exec-1"):
                resp = await client.get(f"{base_url}{path}")
                assert resp.status_code == 404, path

            # A drive-by POST is refused on the anti-CSRF header before auth
            # is consulted; a well-formed request without a Bearer still 401s.
            resp = await client.post(
                f"{base_url}/console/sessions",
                json={"agent": agent_harness_env["agent_name"]},
            )
            assert resp.status_code == 403

            resp = await client.post(
                f"{base_url}/console/sessions",
                json={"agent": agent_harness_env["agent_name"]},
                headers={"X-Flux-Console": "1"},
            )
            assert resp.status_code == 401

            headers = {"Authorization": f"Bearer {bearer}", "X-Flux-Console": "1"}
            resp = await client.get(f"{base_url}/console/sessions", headers=headers)
            assert resp.status_code == 200
            assert isinstance(resp.json(), list)

            # A bearer that clears auth reaches endpoint logic: this env
            # registers the chat workflow but no agent row, so the spawn
            # answers "agent not found" rather than 401/403. The full
            # spawn/send/approve flow lives in test_agent_console.py, which
            # seeds agents through the admin API.
            resp = await client.post(
                f"{base_url}/console/sessions",
                json={"agent": agent_harness_env["agent_name"]},
                headers=headers,
            )
            assert resp.status_code == 404, resp.text
            assert "not found" in resp.text.lower()
    finally:
        await _cancel_and_wait(server_task)


@pytest.mark.asyncio
async def test_web_mode_serves_index_and_public_health(agent_harness_env):
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"

    process = AgentProcess(
        agent_name=agent_harness_env["agent_name"],
        server_url=agent_harness_env["server_url"],
        mode="web",
        port=port,
        token=agent_harness_env["token"],
    )
    server_task = asyncio.create_task(process.run())

    try:
        await _wait_ready(f"{base_url}/health")

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{base_url}/")
            assert resp.status_code == 200
            # web mode serves the multi-session console shell (the old
            # single-agent "Flux Agent" page is gone).
            assert "Flux Console" in resp.text
            assert "</html>" in resp.text.lower()

            resp = await client.get(f"{base_url}/health")
            assert resp.status_code == 200
            assert resp.json() == {"status": "ok"}
    finally:
        await _cancel_and_wait(server_task)
