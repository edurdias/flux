from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

from flux.utils import is_hashable
from flux.utils import make_hashable


# Test fixtures and helper classes
@dataclass
class SimpleClass:
    value: int

    def __str__(self):
        return f"SimpleClass(value={self.value})"


class UnhashableClass:
    def __init__(self, value):
        self.value = value

    def __str__(self):
        return f"UnhashableClass({self.value})"

    # Make the class explicitly unhashable
    __hash__ = None  # type: ignore


# Group 1: Basic Types
def test_basic_types():
    """Test that basic types remain unchanged"""
    # Already hashable types should be returned as-is
    assert make_hashable(42) == 42
    assert make_hashable(3.14) == 3.14
    assert make_hashable("hello") == "hello"
    assert make_hashable(True) is True
    assert make_hashable(None) is None
    assert make_hashable((1, 2)) == (1, 2)


# Group 2: Basic Collections
def test_basic_collections():
    """Test conversion of basic collections"""
    # List to tuple
    assert make_hashable([1, 2, 3]) == (1, 2, 3)

    # Set to frozenset
    result = make_hashable({1, 2, 3})
    assert isinstance(result, frozenset)
    assert result == frozenset([1, 2, 3])

    # Dict to tuple of tuples
    result = make_hashable({"a": 1, "b": 2})
    assert result == (("a", 1), ("b", 2))

    # Empty collections
    assert make_hashable([]) == ()
    assert make_hashable({}) == ()
    assert make_hashable(set()) == frozenset()


# Group 3: Nested Structures
def test_nested_structures():
    """Test conversion of nested unhashable structures"""
    # Nested lists
    assert make_hashable([1, [2, 3], [4, [5, 6]]]) == (1, (2, 3), (4, (5, 6)))

    # Nested dicts
    input_dict = {"a": {"b": 1}, "c": {"d": [1, 2]}}
    expected = (("a", (("b", 1),)), ("c", (("d", (1, 2)),)))
    assert make_hashable(input_dict) == expected

    # Mixed nested structures
    complex_struct = {
        "list": [1, 2, {"a": 3}],
        "set": {4, 5, frozenset([6])},
        "dict": {"b": [7, 8]},
    }
    result = make_hashable(complex_struct)
    assert isinstance(result, tuple)
    assert is_hashable(result)


# Group 4: Pandas DataFrames and Series
def test_pandas_dataframe():
    """Test that pandas DataFrames are converted to strings"""
    # Basic DataFrame
    df = pd.DataFrame({"A": [1, 2, 3], "B": ["a", "b", "c"]})
    result = make_hashable(df)
    assert isinstance(result, str)
    assert "A  B" in result  # Basic content check

    # Empty DataFrame
    empty_df = pd.DataFrame()
    assert isinstance(make_hashable(empty_df), str)

    # DataFrame with different data types
    complex_df = pd.DataFrame(
        {"int": [1, 2], "float": [1.1, 2.2], "str": ["a", "b"], "bool": [True, False]},
    )
    result = make_hashable(complex_df)
    assert isinstance(result, str)

    # Test hashability of result
    assert is_hashable(result)


def test_pandas_series():
    """Test that pandas Series are converted to strings"""
    # Simple series
    series = pd.Series([1, 2, 3])
    result = make_hashable(series)
    assert isinstance(result, str)
    assert is_hashable(result)

    # Series with index
    series_with_index = pd.Series([1, 2, 3], index=["a", "b", "c"])
    result = make_hashable(series_with_index)
    assert isinstance(result, str)
    assert is_hashable(result)

    # Series with multiple data types
    mixed_series = pd.Series([1, "two", 3.0, True])
    result = make_hashable(mixed_series)
    assert isinstance(result, str)
    assert is_hashable(result)


def test_pandas_structures_in_collections():
    """Test pandas objects nested in Python collections"""
    # DataFrame in a list
    df = pd.DataFrame({"A": [1, 2], "B": ["a", "b"]})
    lst = [1, df, "test"]
    result = make_hashable(lst)
    assert isinstance(result, tuple)
    assert isinstance(result[1], str)  # DataFrame converted to string

    # DataFrame in a dictionary
    data = {"df": df, "value": 42}
    result = make_hashable(data)
    assert isinstance(result, tuple)
    assert any(isinstance(item[1], str) for item in result)  # DataFrame value is string


def test_pandas_with_nan():
    """Test DataFrames containing NaN values"""
    df = pd.DataFrame({"A": [1, math.nan, 3], "B": ["a", "b", None]})
    result = make_hashable(df)
    assert isinstance(result, str)
    assert is_hashable(result)


# Group 5: Custom Objects
def test_custom_objects():
    """Test conversion of custom objects"""
    # Dataclass
    simple = SimpleClass(42)
    assert make_hashable(simple) == "SimpleClass(value=42)"

    # Explicitly unhashable class
    unhashable = UnhashableClass(42)
    assert make_hashable(unhashable) == "UnhashableClass(42)"

    # List of custom objects
    objects = [SimpleClass(1), UnhashableClass(2)]
    result = make_hashable(objects)
    assert isinstance(result, tuple)
    assert len(result) == 2
    assert result[0] == "SimpleClass(value=1)"
    assert result[1] == "UnhashableClass(2)"


def test_custom_objects_nested():
    """Test nested structures containing custom objects"""
    data = {
        "simple": SimpleClass(1),
        "unhashable": UnhashableClass(2),
        "list": [SimpleClass(3), UnhashableClass(4)],
    }

    result = make_hashable(data)
    assert isinstance(result, tuple)
    # Convert result to dict for easier verification
    result_dict = dict(result)

    assert result_dict["simple"] == "SimpleClass(value=1)"
    assert result_dict["unhashable"] == "UnhashableClass(2)"
    assert isinstance(result_dict["list"], tuple)
    assert result_dict["list"][0] == "SimpleClass(value=3)"
    assert result_dict["list"][1] == "UnhashableClass(4)"


# Group 6: Mixed Types
def test_mixed_types():
    """Test conversion of structures with mixed types"""
    mixed = {
        "numbers": [1, 2.5, complex(1, 2)],
        "strings": ["hello", b"bytes"],
        "others": [SimpleClass(42), UnhashableClass(7)],
        "nested": {"list": [1, 2, 3], "set": {4, 5, 6}},
    }
    result = make_hashable(mixed)
    assert is_hashable(result)

    # Ensure all parts of the structure are hashable
    def check_hashable(obj):
        if isinstance(obj, (tuple, frozenset)):
            return all(check_hashable(item) for item in obj)
        return is_hashable(obj)

    assert check_hashable(result)


# Group 7: Edge Cases
def test_edge_cases():
    """Test edge cases and special situations"""
    # Circular references aren't supported, but we can test deep nesting
    deep_list = []
    current = deep_list
    for _ in range(100):  # Create a deeply nested list
        current.append([])
        current = current[0]

    result = make_hashable(deep_list)
    assert is_hashable(result)

    # Empty containers of different types
    assert make_hashable([]) == ()
    assert make_hashable({}) == ()
    assert make_hashable(set()) == frozenset()

    # Mixed empty and non-empty containers
    assert make_hashable([[], {}, set(), ["not empty"]]) == ((), (), frozenset(), ("not empty",))

    # Special values
    assert make_hashable(float("inf")) == float("inf")
    assert str(make_hashable(float("nan"))) == str(float("nan"))


# Group 8: Consistency
def test_hashable_consistency():
    """Test that the function produces consistent results"""
    # Same input should produce same output
    complex_input = {"list": [1, 2, 3], "set": {4, 5, 6}, "dict": {"a": [7, 8, 9]}}

    result1 = make_hashable(complex_input)
    result2 = make_hashable(complex_input)

    assert result1 == result2
    assert hash(result1) == hash(result2)

    # Order of dictionary items shouldn't matter
    dict1 = {"a": 1, "b": 2}
    dict2 = {"b": 2, "a": 1}
    assert make_hashable(dict1) == make_hashable(dict2)
