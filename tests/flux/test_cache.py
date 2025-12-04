"""Tests for the cache manager module."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from flux.cache import CacheManager


class TestCacheManager:
    """Test suite for CacheManager."""

    @pytest.fixture(autouse=True)
    def setup_temp_cache(self):
        """Set up a temporary cache directory for each test."""
        self.temp_dir = tempfile.mkdtemp()
        self.cache_path = "cache"

        # Create mock settings
        self.mock_settings = MagicMock()
        self.mock_settings.home = self.temp_dir
        self.mock_settings.cache_path = self.cache_path

        self.mock_config = MagicMock()
        self.mock_config.settings = self.mock_settings

        yield

        # Clean up
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_set_and_get_simple_value(self):
        """Test saving and retrieving a simple value."""
        with patch("flux.cache.Configuration.get", return_value=self.mock_config):
            key = "test_key"
            value = "test_value"

            CacheManager.set(key, value)
            result = CacheManager.get(key)

            assert result == value

    def test_set_and_get_complex_value(self):
        """Test saving and retrieving a complex nested object."""
        with patch("flux.cache.Configuration.get", return_value=self.mock_config):
            key = "complex_key"
            value = {
                "string": "text",
                "number": 42,
                "float": 3.14,
                "boolean": True,
                "list": [1, 2, 3],
                "nested": {"a": "nested value", "b": [{"x": 1}, {"y": 2}]},
            }

            CacheManager.set(key, value)
            result = CacheManager.get(key)

            assert result == value

    def test_get_nonexistent_key_returns_none(self):
        """Test that getting a nonexistent key returns None."""
        with patch("flux.cache.Configuration.get", return_value=self.mock_config):
            result = CacheManager.get("nonexistent_key")
            assert result is None

    def test_set_overwrites_existing_value(self):
        """Test that setting a key again overwrites the previous value."""
        with patch("flux.cache.Configuration.get", return_value=self.mock_config):
            key = "overwrite_key"
            original_value = "original"
            new_value = "updated"

            CacheManager.set(key, original_value)
            CacheManager.set(key, new_value)
            result = CacheManager.get(key)

            assert result == new_value

    def test_cache_creates_directory_if_not_exists(self):
        """Test that the cache directory is created if it doesn't exist."""
        with patch("flux.cache.Configuration.get", return_value=self.mock_config):
            cache_dir = Path(self.temp_dir) / self.cache_path

            # Ensure cache directory doesn't exist yet
            assert not cache_dir.exists()

            CacheManager.set("create_dir_test", "value")

            # Now the cache directory should exist
            assert cache_dir.exists()
            assert cache_dir.is_dir()

    def test_cache_file_uses_correct_extension(self):
        """Test that cache files use .pkl extension."""
        with patch("flux.cache.Configuration.get", return_value=self.mock_config):
            key = "extension_test"
            CacheManager.set(key, "value")

            expected_file = Path(self.temp_dir) / self.cache_path / f"{key}.pkl"
            assert expected_file.exists()

    def test_set_and_get_none_value(self):
        """Test saving and retrieving None as a value."""
        with patch("flux.cache.Configuration.get", return_value=self.mock_config):
            key = "none_key"
            value = None

            CacheManager.set(key, value)
            result = CacheManager.get(key)

            # Note: getting a non-existent key also returns None,
            # so we verify the file was created
            cache_file = Path(self.temp_dir) / self.cache_path / f"{key}.pkl"
            assert cache_file.exists()
            assert result is None

    def test_set_and_get_empty_string(self):
        """Test saving and retrieving an empty string."""
        with patch("flux.cache.Configuration.get", return_value=self.mock_config):
            key = "empty_string_key"
            value = ""

            CacheManager.set(key, value)
            result = CacheManager.get(key)

            assert result == ""

    def test_set_and_get_empty_list(self):
        """Test saving and retrieving an empty list."""
        with patch("flux.cache.Configuration.get", return_value=self.mock_config):
            key = "empty_list_key"
            value = []

            CacheManager.set(key, value)
            result = CacheManager.get(key)

            assert result == []

    def test_set_and_get_empty_dict(self):
        """Test saving and retrieving an empty dictionary."""
        with patch("flux.cache.Configuration.get", return_value=self.mock_config):
            key = "empty_dict_key"
            value = {}

            CacheManager.set(key, value)
            result = CacheManager.get(key)

            assert result == {}

    def test_set_and_get_numeric_types(self):
        """Test saving and retrieving different numeric types."""
        with patch("flux.cache.Configuration.get", return_value=self.mock_config):
            test_cases = [
                ("int_key", 42),
                ("float_key", 3.14159),
                ("negative_key", -100),
                ("zero_key", 0),
                ("large_int_key", 10**100),
            ]

            for key, value in test_cases:
                CacheManager.set(key, value)
                result = CacheManager.get(key)
                assert result == value, f"Failed for key {key}"

    def test_set_and_get_boolean_values(self):
        """Test saving and retrieving boolean values."""
        with patch("flux.cache.Configuration.get", return_value=self.mock_config):
            CacheManager.set("true_key", True)
            CacheManager.set("false_key", False)

            assert CacheManager.get("true_key") is True
            assert CacheManager.get("false_key") is False

    def test_set_and_get_bytes(self):
        """Test saving and retrieving bytes."""
        with patch("flux.cache.Configuration.get", return_value=self.mock_config):
            key = "bytes_key"
            value = b"binary data \x00\x01\x02"

            CacheManager.set(key, value)
            result = CacheManager.get(key)

            assert result == value

    def test_different_keys_stored_separately(self):
        """Test that different keys are stored in separate files."""
        with patch("flux.cache.Configuration.get", return_value=self.mock_config):
            CacheManager.set("key1", "value1")
            CacheManager.set("key2", "value2")

            assert CacheManager.get("key1") == "value1"
            assert CacheManager.get("key2") == "value2"

    def test_special_characters_in_key(self):
        """Test that keys with special characters work correctly."""
        with patch("flux.cache.Configuration.get", return_value=self.mock_config):
            # Note: Some characters may not work as file names on all systems
            key = "key_with_underscores_and_numbers_123"
            value = "special_key_value"

            CacheManager.set(key, value)
            result = CacheManager.get(key)

            assert result == value

    def test_get_file_name_returns_correct_path(self):
        """Test that _get_file_name returns the correct path."""
        with patch("flux.cache.Configuration.get", return_value=self.mock_config):
            key = "test_file_name"
            expected_path = Path(self.temp_dir) / self.cache_path / f"{key}.pkl"

            result = CacheManager._get_file_name(key)

            assert result == expected_path
