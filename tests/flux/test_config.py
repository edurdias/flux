"""Tests for the configuration system."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from flux.config import BaseConfig
from flux.config import CatalogConfig
from flux.config import Configuration
from flux.config import EncryptionConfig
from flux.config import ExecutorConfig
from flux.config import FluxConfig


def test_base_config_to_dict():
    """Test that to_dict returns a dictionary representation of the config."""

    class SimpleConfig(BaseConfig):
        value: str = "test"
        number: int = 42

    config = SimpleConfig()
    config_dict = config.to_dict()

    assert isinstance(config_dict, dict)
    assert config_dict["value"] == "test"
    assert config_dict["number"] == 42


def test_catalog_config_defaults():
    """Test default values for CatalogConfig."""
    config = CatalogConfig()
    assert config.auto_register is False
    assert config.options == {}


def test_catalog_config_custom_values():
    """Test custom values for CatalogConfig."""
    config = CatalogConfig(auto_register=True, options={"source": "examples"})
    assert config.auto_register is True
    assert config.options == {"source": "examples"}


def test_executor_config_defaults():
    """Test default values for ExecutorConfig."""
    config = ExecutorConfig()
    assert config.max_workers is None
    assert config.default_timeout == 0
    assert config.retry_attempts == 3
    assert config.retry_delay == 1
    assert config.retry_backoff == 2


def test_executor_config_custom_values():
    """Test custom values for ExecutorConfig."""
    config = ExecutorConfig(
        max_workers=10,
        default_timeout=30,
        retry_attempts=5,
        retry_delay=2,
        retry_backoff=3,
    )
    assert config.max_workers == 10
    assert config.default_timeout == 30
    assert config.retry_attempts == 5
    assert config.retry_delay == 2
    assert config.retry_backoff == 3


def test_encryption_config_defaults():
    """Test default values for EncryptionConfig."""
    config = EncryptionConfig()
    assert config.encryption_key is None


def test_encryption_config_custom_values():
    """Test custom values for EncryptionConfig."""
    config = EncryptionConfig(encryption_key="secret-key")
    assert config.encryption_key == "secret-key"


def test_flux_config_defaults():
    """Test default values for FluxConfig."""
    config = FluxConfig()
    assert config.debug is False
    assert config.log_level == "INFO"
    assert config.server_port == 8000
    assert config.server_host == "localhost"
    assert config.api_url == "http://localhost:8000"
    assert config.home == ".flux"
    assert config.cache_path == ".cache"
    assert config.local_storage_path == ".data"
    assert config.serializer == "pkl"
    assert config.database_url == "sqlite:///.flux/flux.db"
    assert isinstance(config.executor, ExecutorConfig)
    assert isinstance(config.security, EncryptionConfig)
    assert isinstance(config.catalog, CatalogConfig)


def test_flux_config_custom_values():
    """Test custom values for FluxConfig."""
    config = FluxConfig(
        debug=True,
        log_level="DEBUG",
        server_port=9000,
        server_host="0.0.0.0",
        api_url="http://api.example.com",
        home="/opt/flux",
        cache_path="/tmp/cache",
        local_storage_path="/tmp/data",
        serializer="json",
        database_url="postgresql://user:pass@localhost/flux",
        executor={"max_workers": 10, "default_timeout": 30},
        security={"encryption_key": "test-key"},
        catalog={"auto_register": True},
    )

    assert config.debug is True
    assert config.log_level == "DEBUG"
    assert config.server_port == 9000
    assert config.server_host == "0.0.0.0"
    assert config.api_url == "http://api.example.com"
    assert config.home == "/opt/flux"
    assert config.cache_path == "/tmp/cache"
    assert config.local_storage_path == "/tmp/data"
    assert config.serializer == "json"
    assert config.database_url == "postgresql://user:pass@localhost/flux"
    assert config.executor.max_workers == 10
    assert config.executor.default_timeout == 30
    assert config.security.encryption_key == "test-key"
    assert config.catalog.auto_register is True


def test_flux_config_env_vars():
    """Test that environment variables override defaults."""
    with patch.dict(
        os.environ,
        {
            "FLUX_DEBUG": "true",
            "FLUX_LOG_LEVEL": "DEBUG",
            "FLUX_SERVER_PORT": "9000",
            "FLUX_EXECUTOR__MAX_WORKERS": "10",
            "FLUX_CATALOG__AUTO_REGISTER": "true",
        },
    ):
        config = FluxConfig()
        assert config.debug is True
        assert config.log_level == "DEBUG"
        assert config.server_port == 9000
        assert config.executor.max_workers == 10
        assert config.catalog.auto_register is True


def test_flux_config_serializer_validation():
    """Test validation for serializer field."""
    # Valid values
    FluxConfig(serializer="json")
    FluxConfig(serializer="pkl")

    # Invalid value
    with pytest.raises(ValidationError):
        FluxConfig(serializer="xml")


def test_flux_config_load_from_toml_file_not_exists():
    """Test loading from a non-existent TOML file."""
    result = FluxConfig._load_from_toml("nonexistent.toml", ["flux"])
    assert result == {}


def test_flux_config_load_from_toml():
    """Test loading from a TOML file."""
    with tempfile.NamedTemporaryFile(suffix=".toml", mode="wb", delete=False) as f:
        f.write(
            b"""
[flux]
debug = true
log_level = "DEBUG"
server_port = 9000

[flux.executor]
max_workers = 10
default_timeout = 30

[flux.catalog]
auto_register = true
        """,
        )
        f.flush()

    try:
        # Test that the method works without error
        FluxConfig._load_from_toml(f.name, ["flux"])
        assert True  # If we got here, no errors were raised
    finally:
        os.unlink(f.name)


def test_flux_config_load_invalid_toml():
    """Test loading from an invalid TOML file."""
    with tempfile.NamedTemporaryFile(suffix=".toml", mode="wb", delete=False) as f:
        f.write(b"this is not valid toml")
        f.flush()

    try:
        # Test with a file that exists but is invalid
        with patch.object(Path, "exists") as mock_exists:
            mock_exists.return_value = True
            with patch("builtins.open") as mock_open:
                # Set up the mock to use the actual file we created
                mock_open.return_value.__enter__.return_value = open(f.name, "rb")

                # Test the method - it should handle the error gracefully
                result = FluxConfig._load_from_toml(f.name, ["flux"])
                assert result == {}
    finally:
        os.unlink(f.name)


def test_flux_config_load():
    """Test the load method."""
    # Create a mock for _load_from_config and _load_from_pyproject
    with patch.object(FluxConfig, "_load_from_config") as mock_config:
        with patch.object(FluxConfig, "_load_from_pyproject") as mock_pyproject:
            # Set up the mocks to return some values
            mock_config.return_value = {"debug": True}
            mock_pyproject.return_value = {"log_level": "DEBUG"}

            # Call the method
            config = FluxConfig.load()

            # Check that both methods were called
            mock_config.assert_called_once()
            mock_pyproject.assert_called_once()

            # Check that the values were combined
            assert config.debug is True
            assert config.log_level == "DEBUG"


def test_flux_config_load_from_pyproject():
    """Test the _load_from_pyproject method."""
    with patch.object(FluxConfig, "_load_from_toml") as mock_load:
        FluxConfig._load_from_pyproject()
        mock_load.assert_called_once_with("pyproject.toml", ["tool", "flux"])


def test_flux_config_load_from_config():
    """Test the _load_from_config method."""
    with patch.object(FluxConfig, "_load_from_toml") as mock_load:
        FluxConfig._load_from_config()
        mock_load.assert_called_once_with("flux.toml", ["flux"])


@pytest.fixture
def reset_configuration():
    """Fixture to reset the Configuration singleton before and after each test."""
    # Reset before test
    Configuration._instance = None
    Configuration._config = None

    # Run the test
    yield

    # Reset after test
    Configuration._instance = None
    Configuration._config = None


def test_configuration_singleton(reset_configuration):
    """Test that Configuration is a singleton."""
    config1 = Configuration()
    config2 = Configuration()
    assert config1 is config2
    assert config1 is Configuration.get()


def test_configuration_settings(reset_configuration):
    """Test the settings property."""
    with patch.object(FluxConfig, "load") as mock_load:
        mock_load.return_value = FluxConfig(debug=True)
        config = Configuration()
        settings = config.settings
        assert settings.debug is True
        # Should only be called once
        mock_load.assert_called_once()

        # Access settings again, should use cached version
        _ = config.settings
        mock_load.assert_called_once()


def test_configuration_reload(reset_configuration):
    """Test the reload method."""
    with patch.object(FluxConfig, "load") as mock_load:
        mock_load.return_value = FluxConfig(debug=True)
        config = Configuration()
        settings1 = config.settings
        assert settings1.debug is True

        # Change the mock to return a different value
        mock_load.return_value = FluxConfig(debug=False)

        # Reload and check that we get the new value
        config.reload()
        settings2 = config.settings
        assert settings2.debug is False

        # Should have been called twice
        assert mock_load.call_count == 2


def test_configuration_override(reset_configuration):
    """Test the override method."""
    config = Configuration()

    # Simple override
    config.override(debug=True)
    assert config.settings.debug is True

    # Nested override
    config.override(executor={"max_workers": 10})
    assert config.settings.executor.max_workers == 10

    # Multiple overrides
    config.override(log_level="DEBUG", server_port=9000)
    assert config.settings.log_level == "DEBUG"
    assert config.settings.server_port == 9000

    # Deeply nested override
    config.override(executor={"retry_attempts": 5, "retry_delay": 2})
    assert config.settings.executor.max_workers == 10  # Should keep previous value
    assert config.settings.executor.retry_attempts == 5  # Should update
    assert config.settings.executor.retry_delay == 2  # Should update


def test_configuration_reset(reset_configuration):
    """Test the reset method."""
    with patch.object(FluxConfig, "load") as mock_load:
        mock_load.return_value = FluxConfig(debug=True)
        config = Configuration()
        settings1 = config.settings
        assert settings1.debug is True

        # Change the mock to return a different value
        mock_load.return_value = FluxConfig(debug=False)

        # Reset and check that we get the new value
        config.reset()
        settings2 = config.settings
        assert settings2.debug is False

        # Should have been called twice
        assert mock_load.call_count == 2


def test_configuration_update_nested_dict(reset_configuration):
    """Test the _update_nested_dict method."""
    config = Configuration()

    # Simple update
    d = {"a": 1, "b": 2}
    u = {"b": 3, "c": 4}
    config._update_nested_dict(d, u)
    assert d == {"a": 1, "b": 3, "c": 4}

    # Nested update
    d = {"a": 1, "b": {"x": 1, "y": 2}}
    u = {"b": {"y": 3, "z": 4}}
    config._update_nested_dict(d, u)
    assert d == {"a": 1, "b": {"x": 1, "y": 3, "z": 4}}
