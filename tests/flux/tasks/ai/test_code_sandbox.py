from __future__ import annotations

import pytest

from flux.tasks.ai.code_sandbox import validate_code, CodeValidationError

ALLOWED = {"now", "parallel", "deps", "delegate"}


def test_accepts_dispatch_lambda():
    validate_code("lambda: now()", ALLOWED)
    validate_code("lambda: parallel(now(), now())", ALLOWED)
    validate_code('lambda: deps["fetch"]', ALLOWED)
    validate_code('lambda: now() if deps["x"] else now()', ALLOWED)


@pytest.mark.parametrize(
    "code",
    [
        "lambda: __import__('os')",
        "lambda: open('/etc/passwd')",
        "lambda: (1).__class__",
        "lambda: sum(range(10**18))",
        "lambda: [0] * 10**9",
        "lambda: [x for x in deps]",
        "lambda: now.__globals__",
        "now()",
        "lambda: missing()",
        'lambda: deps["x"].format(now())',  # str.format MRO-walk via attribute
        "lambda: now().something",  # attribute access on call result
    ],
)
def test_rejects_unsafe(code):
    with pytest.raises(CodeValidationError):
        validate_code(code, ALLOWED)


def test_callproxy_blocks_attribute_access():
    from flux.tasks.ai.code_sandbox import _CallProxy

    def f(x):
        return x + 1

    p = _CallProxy(f)
    assert p(1) == 2
    with pytest.raises(AttributeError):
        _ = p.__globals__


def test_build_sandbox_globals_locks_builtins():
    from flux.tasks.ai.code_sandbox import build_sandbox_globals, _CallProxy

    g = build_sandbox_globals({"now": lambda: 1})
    assert "open" not in g["__builtins__"]
    assert "len" in g["__builtins__"]
    assert isinstance(g["now"], _CallProxy)


def test_run_code_step_returns_value():
    import asyncio
    from flux.tasks.ai.code_sandbox import run_code_step

    out = asyncio.run(run_code_step("lambda: deps['x']", {"deps": {"x": 42}}, timeout=5))
    assert out == 42


def test_run_code_step_awaits_coroutine():
    import asyncio
    from flux.tasks.ai.code_sandbox import run_code_step

    async def double(n):
        return n * 2

    out = asyncio.run(run_code_step("lambda: double(21)", {"double": double}, timeout=5))
    assert out == 42


def test_run_code_step_rejects_tampered_hash():
    import asyncio
    from flux.tasks.ai.code_sandbox import run_code_step, code_hash

    good = "lambda: deps['x']"
    with pytest.raises(Exception):
        asyncio.run(run_code_step(good, {"deps": {"x": 1}}, timeout=5, expected_hash="deadbeef"))
    out = asyncio.run(
        run_code_step(good, {"deps": {"x": 1}}, timeout=5, expected_hash=code_hash(good)),
    )
    assert out == 1
