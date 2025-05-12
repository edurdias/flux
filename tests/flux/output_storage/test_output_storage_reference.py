from __future__ import annotations

from flux.output_storage import OutputStorageReference


def test_to_dict():
    # Test regular case
    ref = OutputStorageReference(
        storage_type="s3",
        reference_id="bucket/path/to/output",
        metadata={"size": 1024},
    )
    result = ref.to_dict()
    assert result["storage_type"] == "s3"
    assert result["reference_id"] == "bucket/path/to/output"
    assert result["metadata"] == {"size": 1024}


def test_to_dict_with_none_metadata():
    # Test with None metadata
    ref = OutputStorageReference(
        storage_type="s3",
        reference_id="bucket/path/to/output",
        metadata=None,
    )
    result = ref.to_dict()
    assert result["metadata"] == {}


def test_from_dict():
    # Test regular case
    data = {
        "storage_type": "s3",
        "reference_id": "bucket/path/to/output",
        "metadata": {"size": 1024},
    }
    ref = OutputStorageReference.from_dict(data)
    assert ref.storage_type == "s3"
    assert ref.reference_id == "bucket/path/to/output"
    assert ref.metadata == {"size": 1024}


def test_from_dict_without_metadata():
    # Test case without metadata
    data = {
        "storage_type": "s3",
        "reference_id": "bucket/path/to/output",
    }
    ref = OutputStorageReference.from_dict(data)
    assert ref.storage_type == "s3"
    assert ref.reference_id == "bucket/path/to/output"
    assert ref.metadata == {}
