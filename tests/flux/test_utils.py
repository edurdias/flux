from __future__ import annotations

import json
import time
import uuid
from datetime import datetime
from datetime import timedelta
from enum import Enum

import pytest

from flux.context import WorkflowExecutionContext
from flux.errors import ExecutionTimeoutError
from flux.utils import call_with_timeout
from flux.utils import FluxEncoder
from flux.utils import is_hashable
from flux.utils import make_hashable
from flux.utils import to_json


def test_call_with_timeout_no_timeout():
    def func():
        return "success"

    result = call_with_timeout(func, "Task", "test_task", "123", 0)
    assert result == "success"


def test_call_with_timeout_within_timeout():
    def func():
        return "success"

    result = call_with_timeout(func, "Task", "test_task", "123", 1)
    assert result == "success"


def test_call_with_timeout_exceeds_timeout():
    def slow_func():
        time.sleep(2)
        return "success"

    with pytest.raises(ExecutionTimeoutError):
        call_with_timeout(slow_func, "Task", "test_task", "123", 1)


def test_make_hashable_basic_types():
    assert make_hashable(1) == 1
    assert make_hashable("test") == "test"
    assert make_hashable(True) is True


def test_make_hashable_collections():
    assert make_hashable({"a": 1, "b": 2}) == (("a", 1), ("b", 2))
    assert make_hashable([1, 2, 3]) == (1, 2, 3)
    assert make_hashable({1, 2, 3}) == frozenset({1, 2, 3})


def test_is_hashable():
    assert is_hashable(1)
    assert is_hashable("test")
    assert is_hashable([1, 2])
    assert is_hashable({"a": 1})


class TestEnum(Enum):
    A = "a"
    B = "b"


def test_flux_encoder():
    test_data = {
        "enum": TestEnum.A,
        "datetime": datetime(2023, 1, 1),
        "timedelta": timedelta(seconds=60),
        "uuid": uuid.uuid4(),
        "context": WorkflowExecutionContext("test", {"input": "test"}, "123", []),
        "exception": ValueError("test error"),
        "callable": lambda x: x,
    }

    encoded = json.dumps(test_data, cls=FluxEncoder)
    decoded = json.loads(encoded)

    assert decoded["enum"] == "a"
    assert "2023-01-01" in decoded["datetime"]
    assert decoded["timedelta"] == 60.0
    assert isinstance(decoded["uuid"], str)
    assert decoded["context"]["name"] == "test"
    assert decoded["exception"]["type"] == "ValueError"
    assert isinstance(decoded["callable"], str)


def test_to_json():
    data = {"test": "value"}
    assert to_json(data) == json.dumps(data, indent=4, cls=FluxEncoder)
