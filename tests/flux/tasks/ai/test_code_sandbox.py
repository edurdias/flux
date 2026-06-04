from __future__ import annotations

import pytest

from flux.tasks.ai.code_sandbox import validate_code, CodeValidationError

ALLOWED = {"now", "parallel", "summarize", "delegate"}

GOOD = """
async def step(deps, input):
    picked = [d for d in deps['fetch'] if d['score'] > 0.5]
    out = [await summarize(d) for d in picked]
    return {'items': out, 'first': input}
"""


def test_accepts_full_async_step():
    validate_code(GOOD, ALLOWED)


def test_accepts_control_flow_and_arithmetic():
    validate_code(
        "async def step(deps, input):\n"
        "    total = 0\n"
        "    for x in deps['nums']:\n"
        "        if x > 0:\n"
        "            total = total + x * 2\n"
        "    return total\n",
        ALLOWED,
    )


@pytest.mark.parametrize(
    "code",
    [
        "async def step(deps, input):\n    return deps['x'].secret\n",
        "async def step(deps, input):\n    return ('{0.__class__}').format(deps)\n",
        "async def step(deps, input):\n    import os\n    return os\n",
        "def helper():\n    return 1\nasync def step(deps, input):\n    return helper()\n",
        "async def step(deps, input):\n    f = lambda: 1\n    return f()\n",
        "async def step(deps, input):\n    return missing()\n",
        "async def step(deps, input):\n    global x\n    return 1\n",
        "x = 1\n",
        "async def step(deps):\n    return 1\n",
        "async def other(deps, input):\n    return 1\n",
        "async def step(deps, input):\n    try:\n        return 1\n    except Exception:\n        return 2\n",
        # name-binding escape: Store target smuggles a forbidden name
        "async def step(deps, input):\n    [__import__ for __import__ in []]\n    return __import__\n",
        # __builtins__ reachable by name + subscript (no attribute access)
        "async def step(deps, input):\n    return __builtins__['__import__']('os')\n",
        # binding a denied non-dunder name via comprehension target
        "async def step(deps, input):\n    [open for open in []]\n    return open\n",
        # getattr would defeat the no-attribute-access model
        "async def step(deps, input):\n    return getattr(deps, 'x')\n",
        # decorator invokes a callable at definition time
        "@parallel\nasync def step(deps, input):\n    return 1\n",
        # default-arg expression
        "async def step(deps, input=1):\n    return 1\n",
        # positional-only args
        "async def step(deps, input, /):\n    return 1\n",
        # dunder name read
        "async def step(deps, input):\n    return deps['x'].__class__\n",
    ],
)
def test_rejects(code):
    with pytest.raises(CodeValidationError):
        validate_code(code, ALLOWED)


def test_denylist_does_not_block_normal_locals():
    validate_code(
        "async def step(deps, input):\n"
        "    open_count = 0\n"
        "    for x in deps['xs']:\n"
        "        open_count = open_count + 1\n"
        "    return open_count\n",
        ALLOWED,
    )


def test_locals_and_loop_targets_resolve():
    validate_code(
        "async def step(deps, input):\n"
        "    acc = [y for y in deps['xs']]\n"
        "    for z in acc:\n"
        "        acc = acc + [z]\n"
        "    return acc\n",
        ALLOWED,
    )


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
    assert g["__builtins__"] == {}
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


def test_validate_code_rejects_builtin_not_in_bindings():
    with pytest.raises(CodeValidationError):
        validate_code("lambda: sum(range(9))", {"now"})


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


def test_run_code_step_times_out():
    import asyncio
    from flux.tasks.ai.code_sandbox import run_code_step

    async def slow():
        await asyncio.sleep(3)
        return 1

    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(run_code_step("lambda: slow()", {"slow": slow}, timeout=1))


def test_deps_attribute_walk_rejected_by_grammar():
    with pytest.raises(CodeValidationError):
        validate_code('lambda: deps["x"].secret', {"deps"})


def test_sanitize_deps_passes_value_only_structures():
    from flux.tasks.ai.code_sandbox import sanitize_deps

    src = {"a": 1, "b": [1, "x", True, None], "c": {"d": 2.5}}
    assert sanitize_deps(src) == src
    assert sanitize_deps((1, 2)) == [1, 2]
    assert sorted(sanitize_deps({1, 2, 3})) == [1, 2, 3]


def test_sanitize_deps_dumps_pydantic_and_dataclass():
    from dataclasses import dataclass
    from pydantic import BaseModel
    from flux.tasks.ai.code_sandbox import sanitize_deps

    class M(BaseModel):
        x: int
        y: str

    @dataclass
    class D:
        a: int
        b: list

    assert sanitize_deps(M(x=1, y="z")) == {"x": 1, "y": "z"}
    assert sanitize_deps(D(a=1, b=[2, 3])) == {"a": 1, "b": [2, 3]}


def test_sanitize_deps_rejects_nested_callable():
    from flux.tasks.ai.code_sandbox import sanitize_deps

    with pytest.raises(CodeValidationError):
        sanitize_deps({"t": {"reader": open}})


def test_sanitize_deps_rejects_opaque_object():
    from flux.tasks.ai.code_sandbox import sanitize_deps

    class Opaque:
        pass

    with pytest.raises(CodeValidationError):
        sanitize_deps(Opaque())
