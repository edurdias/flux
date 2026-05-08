from __future__ import annotations

import json
import uuid
from datetime import datetime
from datetime import timedelta
from enum import Enum

import pytest

from flux import ExecutionContext
from flux.utils import FluxEncoder
from flux.utils import is_hashable
from flux.utils import make_hashable
from flux.utils import parse_duration
from flux.utils import to_json


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
    assert not is_hashable([1, 2])
    assert not is_hashable({"a": 1})


class SampleEnum(Enum):
    A = "a"
    B = "b"


def test_flux_encoder():
    test_data = {
        "enum": SampleEnum.A,
        "datetime": datetime(2023, 1, 1),
        "timedelta": timedelta(seconds=60),
        "uuid": uuid.uuid4(),
        "context": ExecutionContext(
            "test_id",
            "default",
            "test_name",
            {"input": "test"},
            "123",
            [],
        ),
        "exception": ValueError("test error"),
        "callable": lambda x: x,
    }

    encoded = json.dumps(test_data, cls=FluxEncoder)
    decoded = json.loads(encoded)

    assert decoded["enum"] == "a"
    assert "2023-01-01" in decoded["datetime"]
    assert decoded["timedelta"] == 60.0
    assert isinstance(decoded["uuid"], str)
    assert decoded["context"]["workflow_id"] == "test_id"
    assert decoded["context"]["workflow_name"] == "test_name"
    assert decoded["exception"]["type"] == "ValueError"
    assert isinstance(decoded["callable"], str)


def test_to_json():
    data = {"test": "value"}
    assert to_json(data) == json.dumps(data, indent=4, cls=FluxEncoder)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("30s", timedelta(seconds=30)),
        ("5m", timedelta(minutes=5)),
        ("1h", timedelta(hours=1)),
        ("24h", timedelta(hours=24)),
        ("7d", timedelta(days=7)),
        ("2w", timedelta(weeks=2)),
    ],
)
def test_parse_duration_supported_suffixes(raw, expected):
    assert parse_duration(raw) == expected


@pytest.mark.parametrize("raw", ["", "abc", "1", "1x", "h1", "-1h", "1.5h", "0s", "0h", "0d"])
def test_parse_duration_rejects_invalid(raw):
    with pytest.raises(ValueError):
        parse_duration(raw)
