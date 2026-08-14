"""End-to-end coverage of the agent console against a real Flux stack.

The console is driven in ``api`` mode -- ``flux agent start --mode api`` with
no NAME, spawned as a real subprocess against the session-scoped server +
worker from ``conftest.py`` -- so every assertion here exercises the whole
chain: console endpoint -> ``ConsoleService``/``EventHub`` -> Flux server ->
worker -> execution log.

**No LLM provider is required.** Like ``test_agent_harness_server_modes``,
this module registers a stub ``agents/agent_chat`` that pauses each turn with
a ``chat_response`` output; unlike that one it also runs a real task per turn
(and emits the agent loop's ``tool_start``/``tool_done`` progress payloads)
so tool activity is observable both in the send stream and in the persisted
log the ``/detail`` endpoint returns.
"""

from __future__ import annotations

import json
import socket
import subprocess
import time
from pathlib import Path

import httpx
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURES = Path(__file__).parent / "fixtures"

PLAIN_AGENT = "e2e_console_chat"
CUSTOM_AGENT = "e2e_console_custom"
CUSTOM_WORKFLOW = f"agent_custom_{CUSTOM_AGENT}"

# api mode requires a Bearer token per request (ApiUI._extract_token) even
# when the Flux server itself runs with auth disabled, and every POST/PUT
# additionally requires the console's anti-CSRF header.
AUTH = {"Authorization": "Bearer e2e-console"}
WRITE = {**AUTH, "X-Flux-Console": "1"}

AGENT_CHAT_SRC = '''\
"""Stub ``agents/agent_chat`` for the console E2E — no LLM provider needed.

Each turn runs one real task (``search_docs``) so the persisted log carries
tool-shaped task events, emits the same ``tool_start``/``tool_done`` progress
payloads the real agent loop emits so the send stream carries them live, and
then pauses with a ``chat_response`` output.
"""
from __future__ import annotations

from typing import Any

from flux.agents.types import ChatResponseOutput
from flux.domain.execution_context import ExecutionContext
from flux.task import task
from flux.tasks.pause import pause
from flux.tasks.progress import progress
from flux.workflow import workflow


@task
async def search_docs(query: str) -> str:
    call_id = f"call-{abs(hash(query)) % 10000}"
    await progress(
        {
            "type": "tool_start",
            "id": call_id,
            "name": "search_docs",
            "args": {"query": query},
        },
    )
    result = f"3 hits for {query!r}"
    await progress(
        {"type": "tool_done", "id": call_id, "name": "search_docs", "status": "success"},
    )
    return result


@workflow.with_options(namespace="agents")
async def agent_chat(ctx: ExecutionContext[dict[str, Any]]):
    turn = 0
    response = None
    while True:
        output = ChatResponseOutput(content=response, turn=turn).model_dump()
        next_input = await pause(f"turn_{turn}", output=output)
        message = (next_input or {}).get("message", "")
        hits = await search_docs(message)
        response = f"echo: {message} ({hits})"
        turn += 1
'''

# Uploaded as the custom agent's `workflow_file`; ConsoleService.spawn
# registers it under `agent_custom_<agent>` and runs the session against it,
# so the workflow declared here must carry exactly that name.
CUSTOM_WORKFLOW_SRC = f'''\
"""Per-agent workflow shipped in an agent definition's ``workflow_file``."""
from __future__ import annotations

from typing import Any

from flux.agents.types import ChatResponseOutput
from flux.domain.execution_context import ExecutionContext
from flux.tasks.pause import pause
from flux.workflow import workflow


@workflow.with_options(namespace="agents")
async def {CUSTOM_WORKFLOW}(ctx: ExecutionContext[dict[str, Any]]):
    turn = 0
    response = None
    while True:
        output = ChatResponseOutput(content=response, turn=turn).model_dump()
        next_input = await pause(f"turn_{{turn}}", output=output)
        response = f"custom: {{(next_input or {{}}).get('message', '')}}"
        turn += 1
'''


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _agent_definition(name: str, workflow_file: str | None = None) -> dict:
    definition = {
        "name": name,
        # Never actually called: the stub workflows pause instead of running
        # the agent loop, so no provider is contacted.
        "model": "ollama/stub-model",
        "system_prompt": "You are a stub agent for the console E2E.",
        "description": f"Console E2E agent ({name})",
    }
    if workflow_file is not None:
        definition["workflow_file"] = workflow_file
    return definition


class Console:
    """Thin HTTP wrapper over a running console app in ``api`` mode."""

    def __init__(self, base_url: str, server_url: str):
        self.base_url = base_url
        self.server_url = server_url

    def get(self, path: str, timeout: float = 30.0) -> httpx.Response:
        return httpx.get(f"{self.base_url}{path}", headers=AUTH, timeout=timeout)

    def post(self, path: str, body: dict | None = None, timeout: float = 90.0) -> httpx.Response:
        return httpx.post(
            f"{self.base_url}{path}",
            json=body if body is not None else {},
            headers=WRITE,
            timeout=timeout,
        )

    def put(self, path: str, body: dict, timeout: float = 30.0) -> httpx.Response:
        return httpx.put(f"{self.base_url}{path}", json=body, headers=WRITE, timeout=timeout)

    def json(self, path: str, timeout: float = 30.0):
        response = self.get(path, timeout=timeout)
        response.raise_for_status()
        return response.json()

    def create_session(self, agent: str, name: str | None = None) -> str:
        body: dict = {"agent": agent}
        if name is not None:
            body["name"] = name
        response = self.post("/console/sessions", body)
        assert response.status_code == 200, response.text
        return response.json()["execution_id"]

    def send(self, session_id: str, text: str, timeout: float = 120.0) -> list[dict]:
        """Post one turn and drain its SSE stream into a list of frames."""
        frames: list[dict] = []
        with (
            httpx.Client(timeout=timeout) as client,
            client.stream(
                "POST",
                f"{self.base_url}/console/sessions/{session_id}/send",
                json={"text": text},
                headers=WRITE,
            ) as response,
        ):
            assert response.status_code == 200, response.read()
            for line in response.iter_lines():
                if not line.startswith("data:"):
                    continue
                chunk = line[5:]
                if chunk.startswith(" "):
                    chunk = chunk[1:]
                if not chunk.strip():
                    continue
                try:
                    frames.append(json.loads(chunk))
                except json.JSONDecodeError:
                    continue
        return frames

    def session_row(self, session_id: str) -> dict | None:
        rows = self.json("/console/sessions")
        return next((row for row in rows if row["execution_id"] == session_id), None)

    def wait_for_state(self, session_id: str, target: str, timeout: float = 60.0) -> dict:
        deadline = time.monotonic() + timeout
        row: dict | None = None
        while time.monotonic() < deadline:
            row = self.session_row(session_id)
            if row is not None and row["state"] == target:
                return row
            time.sleep(1)
        raise TimeoutError(
            f"session {session_id} did not reach {target} within {timeout}s (last row: {row})",
        )


def _wait_ready(url: str, timeout: float = 90.0) -> None:
    deadline = time.monotonic() + timeout
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            if httpx.get(url, timeout=2).status_code < 500:
                return
        except httpx.HTTPError as exc:
            last = exc
        time.sleep(0.5)
    raise TimeoutError(f"console at {url} did not become ready within {timeout}s: {last}")


@pytest.fixture(scope="module")
def console(cli, tmp_path_factory):
    """Seed the server for console work, then run the console in api mode.

    The stock harness is not enough here: the console needs agent
    *definitions* to spawn from (a plain one and one shipping a
    ``workflow_file``, so the custom-workflow branch of
    ``ConsoleService.spawn`` is assertable) and a gated workflow so the
    approvals queue has something real to decide.
    """
    tmp = tmp_path_factory.mktemp("agent_console")
    chat_file = tmp / "agent_chat_ns_agents.py"
    chat_file.write_text(AGENT_CHAT_SRC)
    cli.register(str(chat_file))
    # Shared with test_approvals.py; registered (never deleted) here so this
    # module can trip a real approval gate whatever order the files run in.
    cli.register(str(FIXTURES / "approval_workflow.py"))

    for definition in (
        _agent_definition(PLAIN_AGENT),
        _agent_definition(CUSTOM_AGENT, workflow_file=CUSTOM_WORKFLOW_SRC),
    ):
        response = httpx.post(f"{cli.server_url}/admin/agents", json=definition, timeout=30)
        assert response.status_code == 200, response.text

    port = _free_port()
    log_path = tmp / "console.log"
    log = open(log_path, "w")
    proc = subprocess.Popen(
        [
            "poetry",
            "run",
            "flux",
            "agent",
            "start",
            "--mode",
            "api",
            "--port",
            str(port),
            "--server",
            cli.server_url,
        ],
        stdout=log,
        stderr=subprocess.STDOUT,
        cwd=PROJECT_ROOT,
        env=cli._env,
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        _wait_ready(f"{base_url}/health")
    except TimeoutError:
        proc.terminate()
        log.close()
        pytest.fail(f"console did not start\n--- console.log ---\n{log_path.read_text()[-2000:]}")

    yield Console(base_url=base_url, server_url=cli.server_url)

    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    log.close()

    for name in (PLAIN_AGENT, CUSTOM_AGENT):
        try:
            httpx.delete(f"{cli.server_url}/admin/agents/{name}", timeout=30)
        except httpx.HTTPError:
            pass
    # Same reason test_agent_harness_server_modes deletes it: later modules
    # re-register their own agents/agent_chat.
    for ref in (f"agents/{CUSTOM_WORKFLOW}", "agents/agent_chat"):
        try:
            cli.delete(ref)
        except Exception:  # noqa: BLE001 -- teardown must not mask failures
            pass


def _wait_for_pending_approval(cli, exec_id: str, timeout: int = 60) -> dict:
    """Poll the server's approvals listing until ``exec_id`` has a pending row."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        out = cli._server_json(["execution", "approvals", "--execution", exec_id, "--json"])
        pending = [r for r in out.get("approvals", []) if r.get("status") == "pending"]
        if pending:
            return pending[0]
        time.sleep(1)
    raise TimeoutError(f"no pending approval for execution {exec_id} within {timeout}s")


def test_state_reports_a_writable_console(console):
    state = console.json("/console/state")
    assert state["server_url"] == console.server_url
    # NAME omitted: the console serves rail-first with no agent preselected.
    assert state["agent"] is None
    # The probe runs a real cancel against a nonexistent execution; with auth
    # disabled the server answers 404, which means "the write check passed".
    assert state["can_write"] is True
    assert state["missing_permission"] is None


def test_agents_listing_carries_the_seeded_agents(console):
    names = {agent["name"] for agent in console.json("/console/agents")}
    assert {PLAIN_AGENT, CUSTOM_AGENT} <= names


def test_writes_require_the_console_headers(console):
    without_csrf = httpx.post(
        f"{console.base_url}/console/sessions",
        json={"agent": PLAIN_AGENT},
        headers=AUTH,
        timeout=30,
    )
    assert without_csrf.status_code == 403
    assert "X-Flux-Console" in without_csrf.text

    without_auth = httpx.post(
        f"{console.base_url}/console/sessions",
        json={"agent": PLAIN_AGENT},
        headers={"X-Flux-Console": "1"},
        timeout=30,
    )
    assert without_auth.status_code == 401


def test_session_flow_spawn_send_rename_stop(console):
    """The console's core loop, end to end on one session."""
    session_id = console.create_session(PLAIN_AGENT)

    row = console.session_row(session_id)
    assert row is not None, "spawned session missing from /console/sessions"
    assert row["agent_name"] == PLAIN_AGENT
    assert row["workflow_name"] == "agent_chat"
    # Nothing has been said yet, so there is no title to derive.
    assert row["name"] is None
    assert row["derived_title"] is None

    frames = console.send(session_id, "how do schedules work?")
    kinds = [frame["kind"] for frame in frames]
    assert kinds, "send produced no SSE frames"
    # Every turn ends with exactly one reconciliation frame carrying fresh detail.
    assert kinds.count("log_delta") == 1
    assert kinds[-1] == "log_delta"
    assert frames[-1]["data"]["detail"] is not None
    assert "chat_response" in kinds, kinds
    reply = next(f for f in frames if f["kind"] == "chat_response")
    assert "how do schedules work?" in str(reply["data"]["content"])

    # The title is derived from the first user message and cached by the hub,
    # so the (deliberately cheap) list endpoint can show it without a per-row
    # detail fetch.
    row = console.session_row(session_id)
    assert row is not None
    assert row["derived_title"] == "how do schedules work?"

    # Tool activity arrives twice: live, as progress frames on the stream...
    assert "tool_start" in kinds and "tool_done" in kinds, kinds
    tool_start = next(f for f in frames if f["kind"] == "tool_start")
    assert tool_start["data"]["name"] == "search_docs"

    # ...and durably, in the log the detail endpoint returns. Note the log
    # carries only the *completion*: the engine suppresses TASK_STARTED for
    # tasks that begin in a resumed run, which is every tool call an agent
    # makes after its first turn, so both renderers rebuild the tool from
    # this event alone (see derive_blocks / applyDetail).
    detail = console.json(f"/console/sessions/{session_id}/detail")
    events = detail["events"]
    completed = [
        e for e in events if e["type"] == "TASK_COMPLETED" and e.get("name") == "search_docs"
    ]
    assert completed, f"search_docs missing from the log: {[e['type'] for e in events]}"
    # Task outputs are logged as output-storage envelopes, never bare values;
    # both renderers unwrap them (unwrap_output / unwrapOutput) so the panel
    # shows the tool's result instead of storage bookkeeping.
    assert completed[0]["value"]["storage_type"] == "inline"
    assert completed[0]["value"]["metadata"]["value"] == "3 hits for 'how do schedules work?'"

    renamed = console.put(f"/console/sessions/{session_id}/name", {"name": "schedule questions"})
    assert renamed.status_code == 200, renamed.text
    row = console.session_row(session_id)
    assert row is not None
    # An explicit name outranks the derived title everywhere it is shown.
    assert row["name"] == "schedule questions"

    stopped = console.post(f"/console/sessions/{session_id}/stop")
    assert stopped.status_code == 200, stopped.text
    assert stopped.json() == {"execution_id": session_id, "stopped": True}
    console.wait_for_state(session_id, "CANCELLED")


def test_custom_workflow_agent_runs_its_own_workflow(console):
    """An agent shipping a ``workflow_file`` gets its own registered workflow."""
    session_id = console.create_session(CUSTOM_AGENT, name="custom session")

    row = console.session_row(session_id)
    assert row is not None
    assert row["workflow_name"] == CUSTOM_WORKFLOW
    assert row["agent_name"] == CUSTOM_AGENT
    # `name` on the spawn request is the persisted execution name, not a title.
    assert row["name"] == "custom session"

    # Sessions on the custom workflow resume against it, never agent_chat.
    frames = console.send(session_id, "run the custom path")
    reply = next((f for f in frames if f["kind"] == "chat_response"), None)
    assert reply is not None, [f["kind"] for f in frames]
    assert reply["data"]["content"] == "custom: run the custom path"

    console.post(f"/console/sessions/{session_id}/stop")


def test_approval_round_trip_and_second_decide(console, cli):
    """A real gate: it shows up in the queue, decides once, then reports
    ``already_decided`` — the console never resurrects a settled approval."""
    started = cli.run("approval_e2e", '"console-e2e"', mode="async")
    exec_id = started["execution_id"]
    cli.wait_for_state("approval_e2e", exec_id, "PAUSED", timeout=60)
    pending = _wait_for_pending_approval(cli, exec_id)
    call_id = pending["task_call_id"]

    queued = console.json("/console/approvals")
    mine = [row for row in queued if row["execution_id"] == exec_id]
    assert mine, f"pending approval missing from the console queue: {queued}"
    assert mine[0]["task_call_id"] == call_id
    assert mine[0]["task_name"] == "deploy"

    decided = console.post(f"/console/approvals/{exec_id}/{call_id}", {"approve": True})
    assert decided.status_code == 200, decided.text
    assert decided.json() == {"result": "decided"}

    again = console.post(f"/console/approvals/{exec_id}/{call_id}", {"approve": True})
    assert again.status_code == 200, again.text
    assert again.json() == {"result": "already_decided"}

    final = cli.wait_for_state("approval_e2e", exec_id, "COMPLETED", timeout=60)
    assert final["output"] == "deployed:prepared:console-e2e"
