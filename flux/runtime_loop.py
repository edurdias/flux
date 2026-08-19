"""Event-loop selection for the server and worker processes (#261).

uvloop is a drop-in replacement for asyncio's loop. On this project's
benchmarks it did *not* prove faster -- no change to dispatch or
throughput and a slightly worse claim-latency tail (docs/benchmarks) --
which is why `asyncio` is the default and uvloop is opt-in rather than
"on when installed". Other hardware may differ; the point of the setting
is that an operator can measure rather than assume.

It is also not available everywhere: there are no Windows wheels, and a
new CPython release generally ships before uvloop supports it. So the
choice degrades to the stdlib loop rather than refusing to start --
unless an operator named uvloop explicitly, where silently running a
different loop would leave them believing a tuning knob took effect.

`event_loop` (top-level setting) is the knob; see Settings.event_loop.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar

from flux.config import Configuration
from flux.utils import get_logger

logger = get_logger(__name__)

_T = TypeVar("_T")


def _import_uvloop() -> Any | None:
    """Imported through a function so tests can stand in for the platform."""
    try:
        import uvloop
    except ImportError:
        return None
    return uvloop


def uvloop_available() -> bool:
    return _import_uvloop() is not None


def loop_factory() -> Callable[[], asyncio.AbstractEventLoop] | None:
    """A loop factory for ``asyncio.Runner``, or None for the stdlib loop.

    Returning None rather than a factory that builds the default loop
    keeps the caller's behavior identical to what it was before this
    existed: ``asyncio.run(..., loop_factory=None)`` is plain asyncio.run.
    """
    choice = Configuration.get().settings.event_loop
    if choice == "asyncio":
        return None

    uvloop = _import_uvloop()
    if uvloop is None:
        if choice == "uvloop":
            raise RuntimeError(
                "event_loop='uvloop' but uvloop is not installed. Install it "
                "(pip install 'flux-core[uvloop]') or set event_loop='auto'.",
            )
        logger.debug("uvloop not installed; using the asyncio event loop")
        return None

    return uvloop.new_event_loop


def uvicorn_loop_name() -> str:
    """The same choice, as the loop name uvicorn's Config takes."""
    try:
        return "uvloop" if loop_factory() is not None else "asyncio"
    except RuntimeError:
        raise


def run(coro: Coroutine[Any, Any, _T], *, debug: bool | None = None) -> _T:
    """``asyncio.run`` on the configured loop.

    Used by the worker; the server goes through uvicorn, which takes the
    loop by name instead.
    """
    with asyncio.Runner(loop_factory=loop_factory(), debug=debug) as runner:
        return runner.run(coro)
