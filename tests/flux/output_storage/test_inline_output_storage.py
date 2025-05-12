from __future__ import annotations

from flux.output_storage import InlineOutputStorage
from flux.output_storage import OutputStorageReference


def test_store():
    # Test storing simple value
    storage = InlineOutputStorage()
    value = {"key": "value"}
    reference = storage.store("test-id", value)

    assert reference.storage_type == "inline"
    assert reference.reference_id == "test-id"
    assert "value" in reference.metadata
    assert reference.metadata["value"] == value


def test_store_complex_value():
    # Test storing complex object
    storage = InlineOutputStorage()

    class ComplexObject:
        def __init__(self, name):
            self.name = name

    obj = ComplexObject("test")
    reference = storage.store("test-id", obj)

    assert reference.storage_type == "inline"
    assert reference.metadata["value"] is obj  # Should store the actual object


def test_retrieve():
    # Test retrieving stored value
    storage = InlineOutputStorage()
    value = {"key": "value"}
    reference = storage.store("test-id", value)

    retrieved = storage.retrieve(reference)
    assert retrieved == value


def test_delete():
    # Test delete operation (which is a no-op)
    storage = InlineOutputStorage()
    reference = OutputStorageReference(
        storage_type="inline",
        reference_id="test-id",
        metadata={"value": "test"},
    )

    # Should not raise an exception
    storage.delete(reference)
