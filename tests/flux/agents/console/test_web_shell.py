"""The served console shell: local-only assets and both LED-board palettes.

The bundle is plain HTML/CSS/JS with no build step, so the guarantees worth
pinning are the ones a refactor can silently break: the operator's browser
must never be asked to reach a third party (these consoles run against
airgapped servers), and both theme palettes must exist as CSS custom
properties so the `data-theme` toggle has something to switch between.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from flux.agents.ui.web import WebUI

WEB_DIR = Path(__file__).resolve().parents[4] / "flux" / "agents" / "web"

# Locked in the design's Global Constraints -- the console's identity, not
# decoration: ink ground, hairline lines, amber signature, green ok.
DARK_TOKENS = {
    "--ink": "#0b111c",
    "--panel": "#0e1524",
    "--line": "#1c2536",
    "--text": "#c9d2e0",
    "--muted": "#8b96a8",
    "--amber": "#f0a828",
    "--ok": "#4ade80",
}
LIGHT_TOKENS = {
    "--ink": "#f2f0ec",
    "--panel": "#faf8f5",
    "--line": "#ddd8d0",
    "--text": "#3a4150",
    "--muted": "#7a7468",
    "--amber": "#b45309",
    "--ok": "#15803d",
}


@pytest.fixture
def client() -> TestClient:
    ui = WebUI(server_url="http://flux.test", agent_name="coder", operator_token="op-token")
    return TestClient(ui.app, base_url="http://127.0.0.1:8080")


@pytest.fixture
def shell(client: TestClient) -> str:
    response = client.get("/")
    assert response.status_code == 200
    return response.text


@pytest.fixture
def stylesheet() -> str:
    return (WEB_DIR / "console.css").read_text()


def _light_block(css: str) -> str:
    """The `[data-theme='light']` palette block, isolated from the dark one."""
    match = re.search(r"\[data-theme=[\"']light[\"']\]\s*\{(.*?)\}", css, re.DOTALL)
    assert match, "console.css must define a [data-theme='light'] palette block"
    return match.group(1)


def _root_block(css: str) -> str:
    """The bare `:root` palette block -- the dark default."""
    match = re.search(r"(?<!\])\:root\s*\{(.*?)\}", css, re.DOTALL)
    assert match, "console.css must define a :root palette block"
    return match.group(1)


def test_root_serves_the_console_shell(shell: str):
    assert "Flux Console" in shell
    assert "/static/console.css" in shell
    assert "/static/console.js" in shell


def test_shell_references_only_local_assets(shell: str):
    remote = re.findall(r'(?:href|src)\s*=\s*["\'](\s*(?:https?:)?//[^"\']*)["\']', shell)
    assert remote == [], f"console.html must not reference remote assets: {remote}"


def test_bundle_files_are_served_from_the_static_mount(client: TestClient):
    for asset in ("console.css", "console.js"):
        response = client.get(f"/static/{asset}")
        assert response.status_code == 200, asset
        assert response.text.strip(), asset


def test_script_makes_no_third_party_requests():
    script = (WEB_DIR / "console.js").read_text()
    remote = re.findall(r'["\'`](https?:)?//[a-zA-Z0-9]', script)
    assert remote == [], f"console.js must not fetch third-party origins: {remote}"


def test_failed_sessions_are_never_bucketed_with_done():
    """A source-level guard (this repo runs no JS test runner): the rail must
    file failed sessions under their own heading, and must not give them the
    `.done` class, which dims a row to read as finished-normally."""
    script = (WEB_DIR / "console.js").read_text()
    groups = re.search(r"const groups = \[(.*?)\];", script, re.DOTALL)
    assert groups, "buildRail must declare its rail groups"
    assert '"DONE"' in groups.group(1)
    assert '"FAILED"' in groups.group(1)

    row_class = re.search(r"class: `rail-row\$\{[^`]*`", script)
    assert row_class, "rail rows must build their class list"
    assert "failed" not in row_class.group(0)


def test_completions_are_paired_with_their_start_not_reconstructed():
    """Source-level guard mirroring derive_blocks: since #244 the engine
    records a start for every call, including calls that begin in a resumed
    run, so `applyDetail` pairs a terminal event with its start instead of
    inventing a tool from the completion alone."""
    script = (WEB_DIR / "console.js").read_text()
    branch = re.search(
        r'case "TASK_COMPLETED":.*?case "TASK_AWAITING_APPROVAL"',
        script,
        re.DOTALL,
    )
    assert branch, "applyDetail must handle task completion events"
    body = branch.group(0)
    assert "if (!tool) break;" in body, "an unmatched terminal event must not invent a tool"
    assert "tools.set(event.source_id" not in body, "the completion branch must not create tools"
    # The log stores outputs as OutputStorageReference envelopes; the panel
    # shows the value, not the storage bookkeeping.
    assert "unwrapOutput" in body


def test_reducer_drops_frames_addressed_to_another_session():
    """The critical one (this repo runs no JS test runner, so it is pinned at
    the source): `handleEvent` is the only path live frames take into
    `state`, and a turn keeps streaming after the operator switches sessions.
    Without the guard, session A's tokens append to B's transcript and A's
    `log_delta` replaces B's blocks wholesale — permanently, since the
    reducer is what the poll reconciles against."""
    script = (WEB_DIR / "console.js").read_text()
    reducer = re.search(r"function handleEvent\(consoleEvent\) \{(.*?)\n\}", script, re.DOTALL)
    assert reducer, "console.js must define the handleEvent reducer"
    guard = reducer.group(1).lstrip().splitlines()[0]
    assert "consoleEvent.session_id !== state.activeId" in guard, (
        "the first thing handleEvent does must be to drop foreign frames"
    )
    assert "return" in guard


def test_a_turn_is_scoped_to_the_session_it_was_sent_to():
    """`state.activeId` can move under a running turn. Every step after the
    fetch must name the session the turn was sent to, and switching away must
    abort the stream rather than leave it pumping into another session."""
    script = (WEB_DIR / "console.js").read_text()
    send = re.search(r"async function sendMessage\(text\) \{(.*?)\n\}", script, re.DOTALL)
    assert send, "console.js must define sendMessage"
    body = send.group(1)
    assert "const turnId = state.activeId;" in body, "the turn must capture its session id"
    assert "new AbortController()" in body, "the turn must be abortable"
    assert "encodeURIComponent(turnId)" in body, "the turn must POST to its own session"
    assert "state.activeId === turnId" in body, "post-turn writes must check the session"

    open_session = re.search(r"async function openSession\(id\) \{(.*?)\n\}", script, re.DOTALL)
    assert open_session, "console.js must define openSession"
    switched = open_session.group(1)
    assert "inflightTurn.controller.abort()" in switched, "switching must abort the live turn"
    assert "state.busy = false" not in switched, (
        "openSession must not clear busy mid-turn — the turn's own finalizer owns it"
    )


def test_elicitation_replies_target_the_block_own_session():
    """Same class as the frame guard: a prompt is answered against the
    execution that raised it, not whichever session happens to be open."""
    script = (WEB_DIR / "console.js").read_text()
    respond = re.search(
        r"async function respondToElicitation\(block, action\) \{(.*?)\n\}",
        script,
        re.DOTALL,
    )
    assert respond, "console.js must define respondToElicitation"
    body = respond.group(1)
    assert "block.sessionId" in body, "the reply must use the block's own session id"
    assert "api.elicitation(sessionId" in body


def test_stage_header_offers_a_stop_control():
    """`POST /console/sessions/{id}/stop` exists and the spec lists
    stop_session under Commands; the web console must actually expose it —
    guarded (read-only disables it) and confirmed (a cancel is not undoable)."""
    script = (WEB_DIR / "console.js").read_text()
    assert "stop: (id) =>" in script, "the api surface must include stop"
    assert "/stop`" in script
    stop = re.search(r"async function stopSession\(\) \{(.*?)\n\}", script, re.DOTALL)
    assert stop, "console.js must define stopSession"
    body = stop.group(1)
    assert "state.stopConfirm" in body, "stop must be confirmed before it fires"
    assert "isStoppable(row)" in body, "a terminal session has nothing to stop"
    head = re.search(r"function buildStageHead\(\) \{(.*?)\n\}", script, re.DOTALL)
    assert head and "stopSession()" in head.group(1), "the stage header must carry the control"
    assert "guard(" in head.group(1), "the control must be disabled for a read-only token"


def test_a_background_refresh_cannot_erase_an_action_error():
    """A failed stop must be reported *somewhere*.

    The topbar carries one notice line for both background failures and the
    outcome of an operator action, and `stopSession` refreshes the session
    list the moment its POST returns. A refresh that cleared the line
    wholesale on success wiped the 403 the catch had just written, so the
    operator saw the confirm state reset and nothing else. Notices are owned:
    a poller clears only its own.
    """
    script = (WEB_DIR / "console.js").read_text()
    refresh = re.search(r"async function refreshSessions\(\) \{(.*?)\n\}", script, re.DOTALL)
    assert refresh, "console.js must define refreshSessions"
    body = refresh.group(1)
    assert "state.notice = null" not in body, (
        "a background refresh must not clear notices it does not own"
    )
    assert 'clearNotice("sessions")' in body

    stop = re.search(r"async function stopSession\(\) \{(.*?)\n\}", script, re.DOTALL)
    assert stop, "console.js must define stopSession"
    assert 'setNotice("stop"' in stop.group(1), "a failed stop must own its notice"


def test_dark_palette_defined_as_custom_properties(stylesheet: str):
    block = _root_block(stylesheet)
    for token, value in DARK_TOKENS.items():
        assert f"{token}: {value}" in block, f"missing dark {token}"


def test_light_palette_defined_as_custom_properties(stylesheet: str):
    block = _light_block(stylesheet)
    for token, value in LIGHT_TOKENS.items():
        assert f"{token}: {value}" in block, f"missing light {token}"


def test_amber_glow_is_a_dark_only_token(stylesheet: str):
    """The glow is the one signature effect, and it is dark-only by design --
    light drops it rather than restating it, so it has to be a token the
    light block overrides to `none`."""
    assert "--glow: 0 0 12px" in _root_block(stylesheet)
    assert "--glow: none" in _light_block(stylesheet)


def test_shell_never_interpolates_the_agent_name():
    """The old single-agent page templated the agent name into the served
    HTML (an XSS surface it had to escape). The console reads it from
    /console/state instead, so the shell must stay a static file."""
    assert "{{" not in (WEB_DIR / "console.html").read_text()


# ---------------------------------------------------------------------------
# Web console polish (#245) -- source-level guards for the DOM-bound parts.
# The pure helpers these lean on are exercised for real in test_web_text.py.
# ---------------------------------------------------------------------------


def test_the_console_shares_one_truncation_implementation():
    """A second copy is how the client and server drifted apart in the first
    place: the helpers live in text.js, which is also what the parity tests
    import."""
    script = (WEB_DIR / "console.js").read_text()
    assert 'from "./text.js"' in script
    assert not re.search(r"^function truncate\(", script, re.MULTILINE)
    assert not re.search(r"^function sizeOf\(", script, re.MULTILINE)


def test_text_helpers_are_served_from_the_static_mount(client: TestClient):
    response = client.get("/static/text.js")
    assert response.status_code == 200


def test_tool_arguments_are_truncated_like_outputs():
    """A tool called with a pasted file arrives as one multi-megabyte <pre>
    and janks the page on every re-render, so args carry the same budget and
    the same show-full control that outputs do."""
    script = (WEB_DIR / "console.js").read_text()
    assert "ARGS_LIMIT" in script
    call = re.search(r'appendPayload\(body, "Args".*?\);', script, re.DOTALL)
    assert call, "the args panel must go through the shared payload renderer"
    assert "ARGS_LIMIT" in call.group(0)
    # And the shared renderer measures in the unit its label reports.
    renderer = re.search(r"function appendPayload\(.*?\n}", script, re.DOTALL)
    assert renderer, "appendPayload must exist"
    assert "byteSize(text)" in renderer.group(0)
    assert "sliceBytes(text, limit)" in renderer.group(0)
    assert "text.slice(0," not in renderer.group(0)


def test_a_csrf_refusal_never_latches_the_console_read_only():
    """The CSRF gate answers 403 for a missing header or foreign Origin.
    Neither says anything about what the token may do, so treating one as a
    permission denial leaves the console read-only until a page reload."""
    script = (WEB_DIR / "console.js").read_text()
    note = re.search(r"function noteFailure\(.*?\n}", script, re.DOTALL)
    assert note, "noteFailure must exist"
    assert "isCsrfRefusal(body)" in note.group(0)
    classifier = re.search(r"function isCsrfRefusal\(.*?\n}", script, re.DOTALL)
    assert classifier, "the classifier must exist"
    assert "X-Flux-Console" in classifier.group(0)
    assert "Origin not allowed" in classifier.group(0)


def test_the_read_only_tooltip_invalidates_its_regions():
    """missingPermission is learned after the first render -- the probe's
    denial is the only thing that names it -- so a region that memoizes
    without it keeps showing the generic tooltip."""
    script = (WEB_DIR / "console.js").read_text()
    rail = re.search(r'memo\(\s*"rail",\s*\[(.*?)\]', script, re.DOTALL)
    assert rail and "state.missingPermission" in rail.group(1)
    drawer = re.search(r"const signature = JSON\.stringify\(\[(.*?)\]\)", script, re.DOTALL)
    assert drawer and "state.missingPermission" in drawer.group(1)


def test_overlays_trap_focus_and_give_it_back():
    """An overlay a keyboard operator can Tab out of puts the cursor on
    controls behind it -- inert to the eye, live to the keyboard."""
    script = (WEB_DIR / "console.js").read_text()
    assert "function trapFocus(" in script
    keydown = re.search(r'document\.addEventListener\("keydown".*?\n}\);', script, re.DOTALL)
    assert keydown, "the document keydown handler must exist"
    assert 'event.key === "Tab"' in keydown.group(0)
    # Conditioned on the overlay actually being open -- a call parked behind
    # a dead branch reads the same to a substring check.
    assert re.search(
        r"if \(state\.modalOpen\) trapFocus\(dom\.modal, event\)",
        keydown.group(0),
    )
    assert re.search(
        r"if \(state\.drawerOpen\) trapFocus\(dom\.drawer, event\)",
        keydown.group(0),
    )
    # Opening stores what to give focus back to; closing does give it back.
    # The capture itself lives in rememberFocus (see the exclusivity test),
    # which both overlays call.
    assert script.count("rememberFocus();") == 2
    assert script.count("restoreFocus();") == 2
    assert "focusFirstIn(dom.modal)" in script
    assert "focusFirstIn(dom.drawer)" in script


def test_only_one_overlay_is_open_at_a_time():
    """Both overlays share one focus-return target, so two open at once means
    whichever closes second returns focus to nothing."""
    script = (WEB_DIR / "console.js").read_text()
    modal = re.search(r"function openModal\(.*?\n}", script, re.DOTALL)
    drawer = re.search(r"function openDrawer\(.*?\n}", script, re.DOTALL)
    assert modal and "state.drawerOpen = false;" in modal.group(0)
    assert drawer and "state.modalOpen = false;" in drawer.group(0)
    # And the target is captured once, by whichever overlay opened first.
    assert "function rememberFocus(" in script
    assert script.count("focusReturn = document.activeElement") == 1


def test_the_rail_says_when_it_is_showing_a_subset():
    """The sessions listing is one server page (limit 50 by default). Without
    a truncation line, a session that exists but sorted past the cut reads as
    one that is gone (#245)."""
    script = (WEB_DIR / "console.js").read_text()

    assert "X-Flux-Session-Total" in script, "the client must read the total header"
    assert re.search(r"state\.sessionTotal > state\.sessions\.length", script), (
        "the rail must compare the page against the total"
    )
    rail = re.search(r"function buildRail\(.*?\n}", script, re.DOTALL)
    assert rail and "rail-truncated" in rail.group(0)
    # And the memo has to include it, or the line never re-renders.
    memo = re.search(r'memo\(\s*"rail",\s*\[(.*?)\]', script, re.DOTALL)
    assert memo and "state.sessionTotal" in memo.group(1)
