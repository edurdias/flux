"""Event-loop selection (#261).

uvloop is a drop-in replacement for asyncio's loop, but it is not
available everywhere (no Windows wheels, and a new CPython release
usually ships before uvloop supports it). Selection therefore has to
degrade rather than crash -- except when an operator asked for it by
name, where silently running the slower loop would hide the fact that
their tuning did nothing.
"""

from __future__ import annotations

import asyncio

import pytest

from flux.config import Configuration
from flux.runtime_loop import loop_factory, uvloop_available


@pytest.fixture(autouse=True)
def _reset_config():
    yield
    Configuration.get().reset()


def test_auto_uses_uvloop_when_it_is_installed():
    Configuration.get().override(event_loop="auto")

    factory = loop_factory()

    if uvloop_available():
        assert factory is not None
        loop = factory()
        try:
            assert "uvloop" in type(loop).__module__
        finally:
            loop.close()
    else:
        assert factory is None


def test_auto_falls_back_to_asyncio_when_uvloop_is_missing(monkeypatch):
    """A platform without uvloop must still start."""
    Configuration.get().override(event_loop="auto")
    monkeypatch.setattr("flux.runtime_loop._import_uvloop", lambda: None)

    assert loop_factory() is None


def test_asyncio_never_uses_uvloop_even_when_installed():
    Configuration.get().override(event_loop="asyncio")

    assert loop_factory() is None


def test_the_default_is_the_stdlib_loop():
    """Installing the uvloop extra must not change behavior on its own:
    the default follows the measurement (docs/benchmarks), and opting in
    is an explicit setting."""
    assert Configuration.get().settings.event_loop == "asyncio"
    assert loop_factory() is None


def test_uvloop_by_name_is_an_error_when_it_is_missing(monkeypatch):
    """Asking for uvloop explicitly and silently getting the stdlib loop
    would leave an operator believing a tuning knob took effect."""
    Configuration.get().override(event_loop="uvloop")
    monkeypatch.setattr("flux.runtime_loop._import_uvloop", lambda: None)

    with pytest.raises(RuntimeError, match="uvloop"):
        loop_factory()


def test_the_chosen_loop_actually_runs_a_coroutine():
    Configuration.get().override(event_loop="auto")
    factory = loop_factory()

    async def work() -> str:
        return type(asyncio.get_running_loop()).__module__

    with asyncio.Runner(loop_factory=factory) as runner:
        module = runner.run(work())

    assert module.startswith("uvloop" if uvloop_available() else "asyncio")


def test_uvicorn_loop_name_matches_the_selection():
    """uvicorn takes a loop by name rather than a factory."""
    from flux.runtime_loop import uvicorn_loop_name

    Configuration.get().override(event_loop="asyncio")
    assert uvicorn_loop_name() == "asyncio"

    Configuration.get().override(event_loop="auto")
    assert uvicorn_loop_name() == ("uvloop" if uvloop_available() else "asyncio")
