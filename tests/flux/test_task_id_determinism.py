"""Cross-process determinism guard for task IDs.

Mirrors ``tests/flux/domain/test_event_id_determinism.py``. ``task_id`` is the
``source_id`` the replay short-circuit matches on (``flux/task.py``) and the
``call_id`` the approval gate locks on. Built on Python's per-process-randomized
``hash()`` it diverged across worker processes, so completed tasks re-executed
and approvals re-paused on resume. It is now SHA256-derived over a canonical
argument form, so the IDs below hold across any fresh interpreter run.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from flux.task import task
from flux.utils import get_func_args, make_deterministic


async def _sample(a, b):
    return a + b


def _compute(args, kwargs):
    return task._compute_task_id("sample", get_func_args(_sample, args), args, kwargs)


_SUBPROC = (
    "from flux.task import task\n"
    "from flux.utils import get_func_args\n"
    "async def _sample(a, b):\n"
    "    return a + b\n"
    "args = (1, 2)\n"
    "kwargs = {'opts': {'nested': [1, 2, 3], 'flag': True}}\n"
    "print(task._compute_task_id('sample', get_func_args(_sample, args), args, kwargs))\n"
)


def _run(seed: int) -> str:
    env = {**os.environ, "PYTHONHASHSEED": str(seed)}
    return subprocess.check_output([sys.executable, "-c", _SUBPROC], text=True, env=env).strip()


def test_task_id_is_stable_across_processes():
    """The same call computes the same task_id under any PYTHONHASHSEED."""
    ids = {_run(0), _run(1), _run(42), _run(12345)}
    assert len(ids) == 1, f"task_id diverged across processes: {ids}"


def test_task_id_matches_in_process_and_subprocess():
    args = (1, 2)
    kwargs = {"opts": {"nested": [1, 2, 3], "flag": True}}
    assert _compute(args, kwargs) == _run(0)


def test_task_id_stable_across_calls():
    assert _compute((1, 2), {}) == _compute((1, 2), {})


def test_task_id_differs_for_different_args():
    assert _compute((1, 2), {}) != _compute((1, 3), {})
    assert _compute((1, 2), {}) != _compute((2, 1), {})


def test_task_id_has_name_prefix():
    assert _compute((1, 2), {}).startswith("sample_")


def test_task_id_is_value_based_for_distinct_equal_objects():
    """Two distinct-but-equal object args yield the same task_id (not address-based)."""

    class Cfg:
        def __init__(self, v):
            self.v = v

    id1 = task._compute_task_id("t", {"c": Cfg(1)}, (Cfg(1),), {})
    id2 = task._compute_task_id("t", {"c": Cfg(1)}, (Cfg(1),), {})
    assert id1 == id2

    id3 = task._compute_task_id("t", {"c": Cfg(2)}, (Cfg(2),), {})
    assert id1 != id3


def test_make_deterministic_is_value_based_for_objects_with_dict():
    class Point:
        def __init__(self, x, y):
            self.x = x
            self.y = y

    assert make_deterministic(Point(1, 2)) == make_deterministic(Point(1, 2))
    assert make_deterministic(Point(1, 2)) != make_deterministic(Point(1, 3))


def test_make_deterministic_orders_sets_and_dicts_stably():
    assert make_deterministic({1, 2, 3}) == make_deterministic({3, 2, 1})
    assert make_deterministic({"a": 1, "b": 2}) == make_deterministic({"b": 2, "a": 1})


def test_make_deterministic_handles_coroutines_by_value():
    """parallel()/pipeline() pass coroutines as task args; they must canonicalise
    deterministically (same call -> same form) instead of hashing by address."""

    async def greet(name):
        return name

    coros = [greet("x"), greet("x"), greet("y")]
    try:
        same_a = make_deterministic(coros[0])
        same_b = make_deterministic(coros[1])
        diff = make_deterministic(coros[2])
        assert same_a == same_b
        assert same_a != diff
    finally:
        for c in coros:
            c.close()


def test_make_deterministic_handles_string_slots():
    """__slots__ may be a bare string; it must not be iterated character-by-character."""

    class Slotted:
        __slots__ = "value"

        def __init__(self, v):
            self.value = v

    assert make_deterministic(Slotted(1)) == make_deterministic(Slotted(1))
    assert make_deterministic(Slotted(1)) != make_deterministic(Slotted(2))


def test_make_deterministic_coroutine_key_includes_module():
    """Coroutine keys are module-qualified so same-named functions in different
    modules do not collide."""

    async def greet(name):
        return name

    c = greet("x")
    try:
        assert __name__ in make_deterministic(c)["__call__"]
    finally:
        c.close()


def test_make_deterministic_guard_raises_on_opaque_object():
    """Objects with no value-based representation fail loudly instead of
    silently producing an address-dependent id."""
    import threading

    with pytest.raises(TypeError):
        make_deterministic(threading.Lock())
