from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from flux.config import Configuration
from flux.output_storage import InlineOutputStorage
from flux.output_storage import LocalFileStorage
from flux.output_storage import OutputStorage
from flux.output_storage import OutputStorageReference


class TestOutputStorageReference:
    def test_to_dict(self):
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

    def test_to_dict_with_none_metadata(self):
        # Test with None metadata
        ref = OutputStorageReference(
            storage_type="s3",
            reference_id="bucket/path/to/output",
            metadata=None,
        )
        result = ref.to_dict()
        assert result["metadata"] == {}

    def test_from_dict(self):
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

    def test_from_dict_without_metadata(self):
        # Test case without metadata
        data = {
            "storage_type": "s3",
            "reference_id": "bucket/path/to/output",
        }
        ref = OutputStorageReference.from_dict(data)
        assert ref.storage_type == "s3"
        assert ref.reference_id == "bucket/path/to/output"
        assert ref.metadata == {}


class TestInlineOutputStorage:
    def test_store(self):
        # Test storing simple value
        storage = InlineOutputStorage()
        value = {"key": "value"}
        reference = storage.store("test-id", value)

        assert reference.storage_type == "inline"
        assert reference.reference_id == "test-id"
        assert "value" in reference.metadata
        assert reference.metadata["value"] == value

    def test_store_complex_value(self):
        # Test storing complex object
        storage = InlineOutputStorage()

        class ComplexObject:
            def __init__(self, name):
                self.name = name

        obj = ComplexObject("test")
        reference = storage.store("test-id", obj)

        assert reference.storage_type == "inline"
        assert reference.metadata["value"] is obj  # Should store the actual object

    def test_retrieve(self):
        # Test retrieving stored value
        storage = InlineOutputStorage()
        value = {"key": "value"}
        reference = storage.store("test-id", value)

        retrieved = storage.retrieve(reference)
        assert retrieved == value

    def test_delete(self):
        # Test delete operation (which is a no-op)
        storage = InlineOutputStorage()
        reference = OutputStorageReference(
            storage_type="inline",
            reference_id="test-id",
            metadata={"value": "test"},
        )

        # Should not raise an exception
        storage.delete(reference)


class TestLocalFileStorage:
    @pytest.fixture
    def mock_config(self):
        """Set up a mock configuration for testing LocalFileStorage."""
        with tempfile.TemporaryDirectory() as temp_dir:
            mock_settings = MagicMock()
            mock_settings.home = temp_dir
            mock_settings.local_storage_path = "storage"
            mock_settings.serializer = "json"

            with patch.object(Configuration, "get") as mock_get:
                mock_config = MagicMock()
                mock_config.settings = mock_settings
                mock_get.return_value = mock_config
                yield temp_dir

    def test_init(self, mock_config):
        """Test initialization of LocalFileStorage."""
        storage = LocalFileStorage()  # noqa: F841

        # Check if base directory was created
        storage_path = Path(mock_config) / "storage"
        assert storage_path.exists()
        assert storage_path.is_dir()

    def test_store_retrieve_json(self, mock_config):
        """Test storing and retrieving with JSON serialization."""
        storage = LocalFileStorage()
        value = {"key": "value", "nested": {"key2": "value2"}}

        # Store the value
        reference = storage.store("test-json-id", value)
        assert reference.storage_type == "local_file"
        assert reference.reference_id == "test-json-id"
        assert reference.metadata["serializer"] == "json"

        # Check if file exists
        file_path = Path(mock_config) / "storage" / "test-json-id.json"
        assert file_path.exists()

        # Verify file content
        content = file_path.read_bytes()
        assert json.loads(content) == value

        # Test retrieval
        retrieved = storage.retrieve(reference)
        assert retrieved == value

    def test_delete(self, mock_config):
        """Test deleting a stored file."""
        storage = LocalFileStorage()
        value = {"key": "value"}

        # Store first to create a file
        reference = storage.store("test-delete-id", value)
        file_path = Path(mock_config) / "storage" / "test-delete-id.json"
        assert file_path.exists()

        # Delete the file
        storage.delete(reference)
        assert not file_path.exists()

    def test_verify_storage_type_error(self, mock_config):
        """Test error case when storage type is invalid."""
        storage = LocalFileStorage()

        reference = OutputStorageReference(
            storage_type="invalid",
            reference_id="test-id",
            metadata={"serializer": "json"},
        )

        with pytest.raises(ValueError) as exc_info:
            storage.retrieve(reference)

        assert "Invalid storage type: invalid" in str(exc_info.value)

    def test_retrieve_missing_file(self, mock_config):
        """Test error case when trying to retrieve a non-existent file."""
        storage = LocalFileStorage()

        reference = OutputStorageReference(
            storage_type="local_file",
            reference_id="non-existent-id",
            metadata={"serializer": "json"},
        )

        with pytest.raises(FileNotFoundError):
            storage.retrieve(reference)

    def test_retrieve_with_invalid_serializer(self, mock_config):
        """Test error handling when serializer in metadata doesn't match content."""
        storage = LocalFileStorage()
        value = {"key": "value"}

        # Store with JSON serializer
        reference = storage.store("test-invalid-ser", value)

        # Modify the reference to have an incorrect serializer
        reference.metadata["serializer"] = "invalid"

        # This should fail since the content is JSON but we're trying to deserialize with something else
        with pytest.raises(Exception):
            storage.retrieve(reference)


# Test to verify implementations fulfill the abstract class requirements
def test_abstract_methods_implementations():
    """Verify that concrete classes implement all required abstract methods."""
    # This test doesn't call methods, but verifies that implementations exist

    # Test InlineOutputStorage
    inline_storage = InlineOutputStorage()
    assert hasattr(inline_storage, "retrieve")
    assert hasattr(inline_storage, "store")
    assert hasattr(inline_storage, "delete")

    # We can't instantiate the abstract class directly, but we can check methods
    abstract_methods = [
        method
        for method in dir(OutputStorage)
        if not method.startswith("_") and method not in dir(object)
    ]

    # Check that InlineOutputStorage implements all abstract methods
    for method in abstract_methods:
        assert hasattr(inline_storage, method), f"InlineOutputStorage missing {method}"

    # LocalFileStorage can't be tested directly here due to its dependency on Configuration
