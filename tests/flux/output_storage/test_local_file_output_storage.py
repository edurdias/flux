from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from flux.config import Configuration
from flux.output_storage import LocalFileStorage
from flux.output_storage import OutputStorageReference


@pytest.fixture
def mock_config():
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


def test_init(mock_config):
    """Test initialization of LocalFileStorage."""
    storage = LocalFileStorage()  # noqa: F841

    # Check if base directory was created
    storage_path = Path(mock_config) / "storage"
    assert storage_path.exists()
    assert storage_path.is_dir()


def test_store_retrieve_json(mock_config):
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


def test_delete(mock_config):
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


def test_verify_storage_type_error(mock_config):
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


def test_retrieve_missing_file(mock_config):
    """Test error case when trying to retrieve a non-existent file."""
    storage = LocalFileStorage()

    reference = OutputStorageReference(
        storage_type="local_file",
        reference_id="non-existent-id",
        metadata={"serializer": "json"},
    )

    with pytest.raises(FileNotFoundError):
        storage.retrieve(reference)


def test_retrieve_with_invalid_serializer(mock_config):
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
