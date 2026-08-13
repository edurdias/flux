"""Per-role import-graph budgets: which heavy packages each process may load.

Wall-clock startup baselines live in tests/perf/test_t8_startup.py (opt-in);
these are the always-on structural halves — deterministic on any runner. Each
check runs a fresh interpreter so this file's own imports cannot mask a
regression. The child's guards live in test_child_import_graph.py; these
cover the other two roles.

The worker is HTTP-only by design: it holds no database URL (the runner
child's env sanitization even strips one), so the persistence graph loading
there is a layering leak, not a dependency. The server legitimately owns
fastapi + sqlalchemy; its budget guards the reverse direction — worker-only
and optional-extra graphs must not creep into it.
"""

from __future__ import annotations

import subprocess
import sys


def _loads(statement: str, module: str) -> bool:
    code = (
        f"import sys; {statement}; "
        f"print(any(k == {module!r} or k.startswith({module!r} + '.') for k in sys.modules))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.strip().splitlines()[-1] == "True"


class TestWorkerBudget:
    def test_no_persistence_graph(self):
        assert not _loads("import flux.worker", "sqlalchemy")
        assert not _loads("import flux.worker", "flux.models")
        assert not _loads("import flux.worker", "alembic")

    def test_no_server_stack(self):
        assert not _loads("import flux.worker", "fastapi")
        assert not _loads("import flux.worker", "uvicorn")


class TestServerBudget:
    def test_no_ai_provider_sdks(self):
        """The ai extra is optional and provider clients are per-call; the
        server process must not pay for them at startup even when they are
        installed."""
        for sdk in ("openai", "anthropic", "ollama"):
            assert not _loads("import flux.server", sdk), f"server imports {sdk} at startup"

    def test_no_worker_runtime(self):
        assert not _loads("import flux.server", "flux.worker")
        assert not _loads("import flux.server", "flux.runners.loader")
