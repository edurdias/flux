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
    return TestClient(ui.app)


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
