"""Perf suite infrastructure — opt-in gate, marker, session environment.

The suite is excluded by default: CI's unit job (``pytest tests/
--ignore=tests/e2e``) collects this directory, so every test here is skipped
unless explicitly opted in via ``FLUX_PERF=1`` or a ``-m`` expression that
selects the ``perf`` marker. No CI workflow edit needed (PLAN.md changelog-5).

Run with:  FLUX_PERF=1 poetry run pytest tests/perf -v
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

PERF_DIR = Path(__file__).resolve().parent

# Make `fixtures.harness` importable from test modules regardless of pytest
# rootdir/importmode (the suite must stay liftable — no tests/e2e or
# package-relative imports).
if str(PERF_DIR) not in sys.path:
    sys.path.insert(0, str(PERF_DIR))


def _perf_enabled(config) -> bool:
    if os.environ.get("FLUX_PERF"):
        return True
    markexpr = config.getoption("-m", default="") or ""
    return "perf" in markexpr and "not perf" not in markexpr


def pytest_collection_modifyitems(config, items):
    skip = (
        None
        if _perf_enabled(config)
        else pytest.mark.skip(
            reason="perf suite is opt-in: run with FLUX_PERF=1 or -m perf",
        )
    )
    for item in items:
        if PERF_DIR not in Path(str(item.fspath)).parents:
            continue
        item.add_marker(pytest.mark.perf)
        if skip is not None:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def perf_env(tmp_path_factory):
    """One server + one worker for the whole perf session.

    Defaults to a throwaway SQLite file; set FLUX_PERF_DATABASE_URL to run
    the suite against another backend (CI runs it against PostgreSQL, which
    is what production uses).
    """
    from fixtures.harness.env import FluxPerfEnv

    workdir = tmp_path_factory.mktemp("perf")
    env = FluxPerfEnv(
        workdir,
        database_url=os.environ.get("FLUX_PERF_DATABASE_URL"),
    ).start()
    yield env
    env.stop()
    if not os.environ.get("FLUX_PERF_KEEP_LOGS"):
        shutil.rmtree(workdir, ignore_errors=True)


@pytest.fixture(scope="session")
def stream_workflow_source():
    """Path to the synthetic streaming workflow source file."""
    return PERF_DIR / "fixtures" / "stream_workflow.py"


@pytest.fixture(scope="session")
def stream_workflow(perf_env, stream_workflow_source):
    """Register the synthetic streaming workflow; return (namespace, name)."""
    result = perf_env.register(stream_workflow_source)
    entries = result if isinstance(result, list) else [result]
    for entry in entries:
        if isinstance(entry, dict) and entry.get("name") == "perf_stream":
            return entry.get("namespace", "default"), "perf_stream"
    return "default", "perf_stream"


@pytest.fixture(scope="session")
def sidecar_workflow(perf_env):
    """Register the sidecar-tee workflow (T4); return its namespace."""
    result = perf_env.register(PERF_DIR / "fixtures" / "sidecar_workflow.py")
    entries = result if isinstance(result, list) else [result]
    for entry in entries:
        if isinstance(entry, dict) and entry.get("name") == "perf_sidecar_stream":
            return entry.get("namespace", "default")
    return "default"
