from __future__ import annotations

from dataclasses import dataclass

from flux.utils import is_hashable


# Test fixtures and helper classes
@dataclass(frozen=True)
class FrozenDataClass:
    value: int


@dataclass
class MutableDataClass:
    value: int


class HashableClass:
    def __hash__(self):
        return 42


class UnhashableClass:
    __hash__ = None  # type: ignore


# Group 1: Built-in Immutable Types
def test_builtin_immutable_types():
    """Test that built-in immutable types are hashable"""
    assert is_hashable(42)  # int
    assert is_hashable(3.14)  # float
    assert is_hashable("hello")  # str
    assert is_hashable(True)  # bool
    assert is_hashable(None)  # NoneType
    assert is_hashable((1, 2, 3))  # tuple of hashable items
    assert is_hashable(frozenset([1, 2, 3]))  # frozenset


# Group 2: Built-in Mutable Types
def test_builtin_mutable_types():
    """Test that built-in mutable types are not hashable"""
    assert not is_hashable([1, 2, 3])  # list
    assert not is_hashable({1, 2, 3})  # set
    assert not is_hashable({"a": 1, "b": 2})  # dict
    assert not is_hashable(bytearray(b"hello"))  # bytearray


# Group 3: Custom Classes
def test_custom_classes():
    """Test hashability of custom classes"""
    assert is_hashable(HashableClass())  # Class with __hash__ method
    assert not is_hashable(UnhashableClass())  # Class with __hash__ = None
    assert is_hashable(FrozenDataClass(42))  # Frozen dataclass
    assert not is_hashable(MutableDataClass(42))  # Regular dataclass


# Group 4: Complex Nested Structures
def test_nested_structures():
    """Test hashability of nested structures"""
    assert is_hashable((1, (2, 3)))  # Nested tuple of hashables
    assert not is_hashable((1, [2, 3]))  # Tuple containing unhashable
    assert is_hashable(frozenset([1, (2, 3)]))  # Frozenset of hashables
    assert not is_hashable({(1, 2): [3, 4]})  # Dict with unhashable value


# Group 5: Edge Cases
def test_edge_cases():
    """Test edge cases and special situations"""

    # Functions are hashable
    def test_func():
        pass

    assert is_hashable(test_func)

    # Lambda functions are hashable
    assert is_hashable(lambda x: x)

    # Methods are hashable
    assert is_hashable(str.upper)

    # Types themselves are hashable
    assert is_hashable(int)
    assert is_hashable(str)
    assert is_hashable(type)


# Group 6: Special Python Objects
def test_special_objects():
    """Test hashability of special Python objects"""
    assert is_hashable(Ellipsis)  # ...
    assert is_hashable(NotImplemented)

    # Built-in exceptions are hashable
    assert is_hashable(Exception())
    assert is_hashable(ValueError())

    # Module objects are hashable
    import sys

    assert is_hashable(sys)


# Group 7: Corner Cases with None and Empty Collections
def test_corner_cases():
    """Test corner cases with None and empty collections"""
    assert is_hashable(())  # Empty tuple
    assert is_hashable(frozenset())  # Empty frozenset
    assert not is_hashable([])  # Empty list
    assert not is_hashable({})  # Empty dict
    assert not is_hashable(set())  # Empty set
