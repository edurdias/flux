"""The request paths that must not do database work on the event loop (#263).

A sync call inside an async handler does not slow that request down --
it stalls every other request sharing the loop, which is why it shows up
as tail latency somewhere unrelated and is so easy to reintroduce.

This is a source-level guard, deliberately narrow: it names the handlers
whose blocking work was measured and moved, rather than trying to express
"no sync clients anywhere", which would need an allowlist for every cold
path and would rot within a release. The measurement itself lives in
tests/perf/test_b4_loop_responsiveness.py.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

API = Path(__file__).resolve().parents[2] / "flux" / "api"

# (module, handler, the call that must stay off the loop)
HOT_PATHS = [
    ("workflow_routes.py", "workflows_status_ns", "manager.get"),
    ("workflow_routes.py", "workflows_run_ns", "self._create_execution"),
    ("execution_routes.py", "execution_get", "manager.get"),
]


def _handler_source(module: str, handler: str) -> str:
    source = (API / module).read_text()
    start = source.index(f"async def {handler}(")
    # Up to the next route decorator, which is where this handler ends.
    end = source.find("        @api.", start)
    return source[start : end if end != -1 else len(source)]


@pytest.mark.parametrize(("module", "handler", "call"), HOT_PATHS)
def test_hot_handler_keeps_its_blocking_call_off_the_loop(module, handler, call):
    body = _handler_source(module, handler)

    assert call in body, f"{handler} no longer calls {call} -- update this guard"
    unwrapped = re.search(
        rf"(?<!asyncio\.to_thread\(\n {{20}}){re.escape(call)}\(",
        body,
    )
    wrapped = re.search(rf"asyncio\.to_thread\(\s*{re.escape(call)}\b", body)
    assert wrapped, (
        f"{module}::{handler} calls {call}() directly on the event loop; "
        "wrap it in asyncio.to_thread so it cannot stall every other request"
    )
    assert unwrapped is None or wrapped.start() <= unwrapped.start()


def test_the_checkpoint_path_stays_off_the_loop():
    """The highest-frequency write in the system: one call per event."""
    body = _handler_source("worker_routes.py", "workers_checkpoint")

    assert re.search(r"asyncio\.to_thread\(\s*context_manager\.update", body), (
        "the checkpoint handler must not run ContextManager.update on the loop"
    )
