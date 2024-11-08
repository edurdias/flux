from __future__ import annotations

from math import isinf
from math import isnan

from flux.utils import parse_value


def test_none_values():
    assert parse_value(None) is None
    assert parse_value("") is None
    assert parse_value("null") is None
    assert parse_value("None") is None


def test_boolean_values():
    assert parse_value("true") is True
    assert parse_value("True") is True
    assert parse_value("TRUE") is True
    assert parse_value("false") is False
    assert parse_value("False") is False
    assert parse_value("FALSE") is False


def test_integer_values():
    assert parse_value("0") == 0
    assert parse_value("123") == 123
    assert parse_value("-123") == -123
    assert parse_value("+123") == 123


def test_float_values():
    assert parse_value("0.0") == 0.0
    assert parse_value("123.45") == 123.45
    assert parse_value("-123.45") == -123.45
    assert parse_value("+123.45") == 123.45
    assert parse_value("1e-10") == 1e-10
    assert parse_value("1.23e-4") == 1.23e-4


def test_string_values():
    assert parse_value("hello") == "hello"
    assert parse_value("123abc") == "123abc"
    assert parse_value("abc123") == "abc123"
    assert parse_value("hello world") == "hello world"
    assert parse_value("!@#$%") == "!@#$%"


def test_json_values():
    # Objects
    assert parse_value('{"name": "test"}') == {"name": "test"}
    assert parse_value('{"age": 25}') == {"age": 25}
    assert parse_value('{"active": true}') == {"active": True}
    assert parse_value('{"data": null}') == {"data": None}

    # Arrays
    assert parse_value("[1,2,3]") == [1, 2, 3]
    assert parse_value('["a","b","c"]') == ["a", "b", "c"]
    assert parse_value("[true, false]") == [True, False]
    assert parse_value("[null]") == [None]

    # Nested structures
    assert parse_value('{"items": [1,2,3]}') == {"items": [1, 2, 3]}
    assert parse_value('[{"id": 1}, {"id": 2}]') == [{"id": 1}, {"id": 2}]


def test_special_cases():
    # Should remain strings
    assert parse_value("2023-01-01") == "2023-01-01"
    assert parse_value("12:00:00") == "12:00:00"
    assert isinf(parse_value("Infinity"))
    assert isnan(parse_value("NaN"))

    # Should not be parsed as JSON even though they contain braces/brackets
    assert parse_value("a{b}c") == "a{b}c"
    assert parse_value("x[y]z") == "x[y]z"


def test_invalid_json():
    # Invalid JSON should be treated as strings
    assert parse_value("{name: test}") == "{name: test}"
    assert parse_value("[1,2,3") == "[1,2,3"
    assert parse_value('{"key": value}') == '{"key": value}'


def test_whitespace_handling():
    assert parse_value("  123  ") == 123
    assert parse_value(" true ") is True
    assert parse_value(" null ") is None
    assert parse_value(" hello ") == " hello "  # Preserves whitespace for strings


def test_case_sensitive_strings():
    # Only boolean and null keywords should be case-insensitive
    assert parse_value("NULL") is None
    assert parse_value("True") is True
    assert parse_value("FALSE") is False
    assert parse_value("Hello") == "Hello"
    assert parse_value("HELLO") == "HELLO"


def test_invalid_numbers():
    # These should remain strings
    assert parse_value("12.34.56") == "12.34.56"
    assert parse_value("1.2e3.4") == "1.2e3.4"
    assert parse_value("--123") == "--123"
    assert parse_value("++123") == "++123"
