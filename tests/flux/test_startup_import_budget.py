"""Per-role import-graph budgets: which heavy packages each process may load.

Wall-clock startup baselines live in tests/perf/test_t8_startup.py (opt-in);
these are the always-on structural halves — deterministic on any runner. One
fresh interpreter per role (not per assertion — a server import costs ~1s, so
the probes batch every target into a single subprocess) so this file's own
imports cannot mask a regression. The child's guards live in
test_child_import_graph.py; these cover the other two roles.

The worker is HTTP-only by design: it holds no database URL (the runner
child's env sanitization even strips one), so the persistence graph loading
there is a layering leak, not a dependency. The server legitimately owns
fastapi + sqlalchemy; its budget guards the reverse direction — worker-only
and optional-extra graphs must not creep into it.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

_TARGETS = [
    "sqlalchemy",
    "flux.models",
    "alembic",
    "fastapi",
    "uvicorn",
    "openai",
    "anthropic",
    "ollama",
    "flux.worker",
    "flux.runners.loader",
]


def _presence(entry_module: str) -> dict[str, bool]:
    code = (
        f"import sys, json, {entry_module}; "
        f"print(json.dumps({{t: any(k == t or k.startswith(t + '.') for k in sys.modules) "
        f"for t in {_TARGETS!r}}}))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(out.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def worker_modules() -> dict[str, bool]:
    return _presence("flux.worker")


@pytest.fixture(scope="module")
def server_modules() -> dict[str, bool]:
    return _presence("flux.server")


@pytest.fixture(scope="module")
def console_modules() -> dict[str, bool]:
    return _presence("flux.agents.ui.api")


class TestWorkerBudget:
    def test_no_persistence_graph(self, worker_modules):
        assert not worker_modules["sqlalchemy"]
        assert not worker_modules["flux.models"]
        assert not worker_modules["alembic"]

    def test_no_server_stack(self, worker_modules):
        assert not worker_modules["fastapi"]
        assert not worker_modules["uvicorn"]


class TestServerBudget:
    def test_no_ai_provider_sdks(self, server_modules):
        """The ai extra is optional and provider clients are per-call; the
        server process must not pay for them at startup even when they are
        installed."""
        for sdk in ("openai", "anthropic", "ollama"):
            assert not server_modules[sdk], f"server imports {sdk} at startup"

    def test_no_worker_runtime(self, server_modules):
        assert not server_modules["flux.worker"]
        assert not server_modules["flux.runners.loader"]


class TestConsoleBudget:
    def test_console_process_stays_lean(self, console_modules):
        assert not console_modules["sqlalchemy"]
        assert not console_modules["flux.models"]
