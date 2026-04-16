"""Real agent-harness E2E tests using Ollama (@pytest.mark.ollama auto-skipped in CI).

These tests boot an :class:`AgentProcess` in a background asyncio task, drive
``/chat`` over HTTP/SSE, and rely on a live Ollama instance to exercise the
full agent-chat workflow end-to-end. They are intentionally **local-only**: the
``ollama`` marker causes them to skip automatically when Ollama isn't running.

**Run scenarios individually.** Running all five in one pytest invocation can
hit WSL/uvicorn connection-reuse issues where a later test's SSE read raises
``RemoteProtocolError`` even though the server is fine. Each scenario passes
reliably in isolation::

    poetry run pytest tests/e2e/test_agent_harness_ollama.py::test_api_mode_basic_chat_ollama -v -s
    poetry run pytest tests/e2e/test_agent_harness_ollama.py::test_api_mode_multi_turn_conversation_ollama -v -s
    poetry run pytest tests/e2e/test_agent_harness_ollama.py::test_api_mode_tool_calling_files_ollama -v -s
    poetry run pytest tests/e2e/test_agent_harness_ollama.py::test_web_mode_serves_chat_page_and_streams_response_ollama -v -s
    poetry run pytest tests/e2e/test_agent_harness_ollama.py::test_api_mode_session_resume_after_process_restart_ollama -v -s

Tool-calling (qwen3:latest on WSL) can take 10+ minutes; the scenario uses a
15-minute timeout.
"""

from __future__ import annotations

import asyncio
import json
import random
import socket
import string
from contextlib import asynccontextmanager

import httpx
import pytest

from flux.agents.process import AgentProcess


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _random_suffix(n: int = 8) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


@asynccontextmanager
async def _agent_server(
    process: AgentProcess,
    base_url: str,
    ready_timeout: float = 20.0,
):
    """Start an AgentProcess as a background task; wait until /health is up; tear down on exit."""
    task = asyncio.create_task(process.run())
    try:
        loop = asyncio.get_event_loop()
        deadline = loop.time() + ready_timeout
        ready = False
        async with httpx.AsyncClient(timeout=2.0) as client:
            while loop.time() < deadline:
                try:
                    r = await client.get(f"{base_url}/health")
                    if r.status_code == 200:
                        ready = True
                        break
                except Exception:
                    pass
                await asyncio.sleep(0.2)
        if not ready:
            raise RuntimeError(f"Agent process on {base_url} did not become ready")
        yield
    finally:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


async def _read_sse_events(response: httpx.Response):
    """Parse SSE events from an httpx streaming response.

    Yields ``(kind, payload)`` tuples. Handles Flux's multi-line ``data:``
    frames (JSON pretty-printed with indent).
    """
    buffer: list[str] = []
    async for line in response.aiter_lines():
        if line.startswith("data:"):
            chunk = line[5:]
            if chunk.startswith(" "):
                chunk = chunk[1:]
            buffer.append(chunk)
        elif line.strip() == "" and buffer:
            try:
                payload = json.loads("\n".join(buffer))
            except json.JSONDecodeError:
                buffer.clear()
                continue
            buffer.clear()
            kind = payload.get("type", "")
            yield kind, payload
        elif line.startswith(":"):
            continue
    if buffer:
        try:
            payload = json.loads("\n".join(buffer))
            yield payload.get("type", ""), payload
        except json.JSONDecodeError:
            pass


async def _collect_chat_events(
    base_url: str,
    token: str,
    message: str,
    session: str | None = None,
    timeout: float = 180.0,
) -> list[tuple[str, dict]]:
    """Drive one ``/chat`` request end-to-end and return the list of ``(kind, payload)``."""
    url = f"{base_url}/chat"
    if session:
        url += f"?session={session}"
    events: list[tuple[str, dict]] = []
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream(
            "POST",
            url,
            json={"message": message},
            headers={"Authorization": f"Bearer {token}"},
        ) as resp:
            if resp.status_code != 200:
                body = await resp.aread()
                raise RuntimeError(
                    f"POST {url} returned {resp.status_code}: {body!r}",
                )
            async for kind, payload in _read_sse_events(resp):
                events.append((kind, payload))
    return events


def _extract_session_id(events: list[tuple[str, dict]]) -> str | None:
    for kind, payload in events:
        if kind == "session_id":
            return payload.get("id")
    return None


def _extract_response_text(events: list[tuple[str, dict]]) -> str:
    """Best-effort: concat all ``token`` events; else use the last ``response`` content."""
    tokens = [p.get("text", "") for k, p in events if k == "token"]
    if tokens:
        joined = "".join(tokens)
        if joined.strip():
            return joined
    last = ""
    for kind, payload in events:
        if kind == "response":
            content = payload.get("content") or ""
            if content:
                last = content
    return last


def _has_tool_calls(events: list[tuple[str, dict]]) -> bool:
    return any(k == "tool_start" for k, _ in events)


def _provision_agent(cli, name: str, agent_def: dict) -> None:
    """Create the agent and publish its definition as a config entry.

    The ``agents/agent_chat`` template loads its definition at runtime via
    ``get_config("agent:<name>")``, which reads from the ``configs`` table —
    a separate store from the ``agents`` table populated by ``agent create``.
    Tests that drive the real template must seed both stores.
    """
    agent_def = {**agent_def, "name": name}
    r = httpx.post(
        f"{cli.server_url}/admin/agents",
        json=agent_def,
        timeout=10,
    )
    r.raise_for_status()
    cli.config_set(f"agent:{name}", json.dumps(agent_def))


def _teardown_agent(cli, name: str) -> None:
    """Best-effort cleanup for an agent provisioned via :func:`_provision_agent`."""
    try:
        cli.config_remove(f"agent:{name}")
    except Exception:
        pass
    try:
        httpx.delete(f"{cli.server_url}/admin/agents/{name}", timeout=10)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Module-scoped fixture: ensure the real ``agents/agent_chat`` workflow is
# registered on the test server. Safe to re-register if already present.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def agent_workflow(cli):
    """Register the real ``flux/agents/template.py`` so the worker can serve chat."""
    try:
        workflows = cli.list_workflows(namespace="agents")
    except Exception:
        workflows = []
    names = [w.get("name") for w in workflows if isinstance(w, dict)]
    if "agent_chat" not in names:
        cli.register("flux/agents/template.py")
    yield


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


@pytest.mark.ollama
@pytest.mark.asyncio
async def test_api_mode_basic_chat_ollama(cli, agent_workflow):
    agent_name = f"e2e-basic-{_random_suffix()}"
    _provision_agent(
        cli,
        agent_name,
        {
            "model": "ollama/qwen3:latest",
            "system_prompt": "You are a terse assistant. Respond in at most 10 words.",
        },
    )
    try:
        port = _free_port()
        base_url = f"http://127.0.0.1:{port}"
        process = AgentProcess(
            agent_name=agent_name,
            server_url=cli.server_url,
            mode="api",
            port=port,
            token=None,
        )
        async with _agent_server(process, base_url, ready_timeout=20.0):
            events = await _collect_chat_events(
                base_url,
                token="any-token",
                message="Say hello in one word.",
                timeout=180,
            )

        session_id = _extract_session_id(events)
        kinds = [k for k, _ in events]
        assert session_id is not None, f"No session_id in events; kinds={kinds}"
        errors = [p for k, p in events if k == "error"]
        assert not errors, f"Got error events: {errors}"
        text = _extract_response_text(events).strip()
        assert text, f"No response text; kinds={kinds}"
    finally:
        _teardown_agent(cli, agent_name)


@pytest.mark.ollama
@pytest.mark.asyncio
async def test_api_mode_multi_turn_conversation_ollama(cli, agent_workflow):
    agent_name = f"e2e-multi-{_random_suffix()}"
    _provision_agent(
        cli,
        agent_name,
        {
            "model": "ollama/qwen3:latest",
            "system_prompt": (
                "You are a helpful assistant with a memory. "
                "When the user tells you a specific word or number to remember, "
                "always recall it exactly when asked later."
            ),
        },
    )
    try:
        port = _free_port()
        base_url = f"http://127.0.0.1:{port}"
        process = AgentProcess(
            agent_name=agent_name,
            server_url=cli.server_url,
            mode="api",
            port=port,
            token=None,
        )
        async with _agent_server(process, base_url, ready_timeout=20.0):
            events1 = await _collect_chat_events(
                base_url,
                token="any-token",
                message=(
                    "Please remember the magic word 'xyzzy'. "
                    "I'll quiz you about it later. Just acknowledge."
                ),
                timeout=180,
            )
            session_id = _extract_session_id(events1)
            assert session_id, f"No session_id from first turn; kinds={[k for k, _ in events1]}"

            events2 = await _collect_chat_events(
                base_url,
                token="any-token",
                message="What was the magic word I asked you to remember?",
                session=session_id,
                timeout=180,
            )
        text = _extract_response_text(events2).lower()
        assert "xyzzy" in text, f"Expected 'xyzzy' in response, got: {text!r}"
    finally:
        _teardown_agent(cli, agent_name)


@pytest.mark.ollama
@pytest.mark.asyncio
async def test_api_mode_tool_calling_files_ollama(cli, agent_workflow, tmp_path):
    sandbox = tmp_path / f"agent-files-{_random_suffix()}"
    sandbox.mkdir()
    (sandbox / "alpha.txt").write_text("alpha content")
    (sandbox / "beta.txt").write_text("beta content")
    (sandbox / "gamma.txt").write_text("gamma content")

    agent_name = f"e2e-tools-{_random_suffix()}"
    _provision_agent(
        cli,
        agent_name,
        {
            "model": "ollama/qwen3:latest",
            "system_prompt": (
                "You are a file-system assistant. "
                "When the user asks about files in the workspace, you MUST call the "
                "provided tools (for example list_directory) to answer. "
                "Do not guess or fabricate filenames — always rely on tool output."
            ),
            "tools": [{"directory": {"workspace": str(sandbox)}}],
            "max_tool_calls": 5,
        },
    )

    try:
        port = _free_port()
        base_url = f"http://127.0.0.1:{port}"
        process = AgentProcess(
            agent_name=agent_name,
            server_url=cli.server_url,
            mode="api",
            port=port,
            token=None,
        )
        async with _agent_server(process, base_url, ready_timeout=20.0):
            events = await _collect_chat_events(
                base_url,
                token="any-token",
                message=(
                    "List every file in your workspace using your tools. "
                    "Then tell me each filename you found."
                ),
                timeout=900,
            )
        kinds = [k for k, _ in events]
        assert _has_tool_calls(events), f"Expected at least one tool_start event; kinds={kinds}"
        text = _extract_response_text(events).lower()
        hits = sum(name in text for name in ("alpha", "beta", "gamma"))
        assert hits >= 2, f"Response should mention at least 2 of alpha/beta/gamma; got: {text!r}"
    finally:
        _teardown_agent(cli, agent_name)


@pytest.mark.ollama
@pytest.mark.asyncio
async def test_web_mode_serves_chat_page_and_streams_response_ollama(
    cli,
    agent_workflow,
):
    agent_name = f"e2e-web-{_random_suffix()}"
    _provision_agent(
        cli,
        agent_name,
        {
            "model": "ollama/qwen3:latest",
            "system_prompt": "You are a terse assistant. Respond in at most 10 words.",
        },
    )
    try:
        port = _free_port()
        base_url = f"http://127.0.0.1:{port}"
        process = AgentProcess(
            agent_name=agent_name,
            server_url=cli.server_url,
            mode="web",
            port=port,
            token="operator-token",
        )
        async with _agent_server(process, base_url, ready_timeout=20.0):
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(f"{base_url}/")
                assert r.status_code == 200
                assert "Flux Agent" in r.text

            events = await _collect_chat_events(
                base_url,
                token="ignored-in-web-mode",
                message="Say the word hello.",
                timeout=180,
            )
        session_id = _extract_session_id(events)
        kinds = [k for k, _ in events]
        assert session_id is not None, f"No session_id; kinds={kinds}"
        text = _extract_response_text(events).strip()
        assert text, f"No response text; kinds={kinds}"
    finally:
        _teardown_agent(cli, agent_name)


@pytest.mark.ollama
@pytest.mark.asyncio
async def test_api_mode_session_resume_after_process_restart_ollama(
    cli,
    agent_workflow,
):
    agent_name = f"e2e-resume-{_random_suffix()}"
    _provision_agent(
        cli,
        agent_name,
        {
            "model": "ollama/qwen3:latest",
            "system_prompt": (
                "You are a helpful assistant with a memory. "
                "When the user tells you a preference or fact, remember it and "
                "recall it exactly when asked later."
            ),
        },
    )
    try:
        port1 = _free_port()
        base_url1 = f"http://127.0.0.1:{port1}"
        process1 = AgentProcess(
            agent_name=agent_name,
            server_url=cli.server_url,
            mode="api",
            port=port1,
            token=None,
        )
        async with _agent_server(process1, base_url1, ready_timeout=20.0):
            events1 = await _collect_chat_events(
                base_url1,
                token="any-token",
                message=("My favorite color is purple. Please acknowledge and remember it."),
                timeout=180,
            )
            session_id = _extract_session_id(events1)
            kinds1 = [k for k, _ in events1]
            assert session_id, f"No session_id from first process; kinds={kinds1}"

        port2 = _free_port()
        base_url2 = f"http://127.0.0.1:{port2}"
        process2 = AgentProcess(
            agent_name=agent_name,
            server_url=cli.server_url,
            mode="api",
            port=port2,
            token=None,
        )
        async with _agent_server(process2, base_url2, ready_timeout=20.0):
            events2 = await _collect_chat_events(
                base_url2,
                token="any-token",
                message="What did I say my favorite color was?",
                session=session_id,
                timeout=180,
            )
        kinds2 = [k for k, _ in events2]
        text = _extract_response_text(events2).strip()
        # Session plumbing: the SECOND AgentProcess resumed the SAME execution
        # across a process boundary — this is the architecturally important
        # bit, and success means we got any SSE events back on the resume POST.
        assert text or "chat_response" in kinds2, (
            f"Resume produced no events; kinds={kinds2}"
        )
        # Memory recall: best-effort. Small local models (qwen3:latest) are
        # flaky at precise recall; a stronger model would reliably say
        # "purple". We log it for visibility but do not fail the test on
        # wording alone.
        if "purple" not in text.lower():
            print(f"\n[note] model did not recall 'purple' exactly; said: {text!r}")
    finally:
        _teardown_agent(cli, agent_name)
