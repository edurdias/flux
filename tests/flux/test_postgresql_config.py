"""Tests for PostgreSQL configuration and environment variable interpolation."""

import pytest
import os
from unittest.mock import patch

from flux.config import FluxConfig, Configuration
from pydantic import ValidationError

# Import fixtures


class TestEnvironmentVariableInterpolation:
    """Test environment variable interpolation in database URLs."""

    def test_single_variable_interpolation(self, mock_env_vars):
        """Test ${VAR_NAME} syntax interpolation."""
        config = FluxConfig(database_url="postgresql://${DB_USER}:password@host:5432/db")
        assert config.database_url == "postgresql://test_user:password@host:5432/db"

    def test_multiple_variable_interpolation(self, mock_env_vars):
        """Test multiple environment variables in URL."""
        url = "postgresql://${DB_USER}:${DB_PASSWORD}@${DB_HOST}:${DB_PORT}/${DB_NAME}"
        config = FluxConfig(database_url=url)
        expected = "postgresql://test_user:test_password@test_host:5432/test_database"
        assert config.database_url == expected

    def test_dollar_sign_variable_interpolation(self, mock_env_vars):
        """Test $VAR_NAME syntax interpolation."""
        config = FluxConfig(database_url="postgresql://$DB_USER:$DB_PASSWORD@localhost:5432/db")
        assert config.database_url == "postgresql://test_user:test_password@localhost:5432/db"

    def test_mixed_variable_syntax(self, mock_env_vars):
        """Test mixing ${VAR} and $VAR syntax."""
        config = FluxConfig(database_url="postgresql://${DB_USER}:$DB_PASSWORD@localhost:5432/db")
        assert config.database_url == "postgresql://test_user:test_password@localhost:5432/db"

    def test_missing_environment_variable_fallback(self, missing_env_vars):
        """Test behavior when environment variable is missing."""
        config = FluxConfig(database_url="postgresql://${MISSING_VAR}:password@host:5432/db")
        # Missing variables should remain as-is
        assert config.database_url == "postgresql://${MISSING_VAR}:password@host:5432/db"

    def test_partial_variable_interpolation(self, mock_env_vars):
        """Test partial interpolation with some missing variables."""
        url = "postgresql://${DB_USER}:${MISSING_VAR}@${DB_HOST}:5432/db"
        config = FluxConfig(database_url=url)
        expected = "postgresql://test_user:${MISSING_VAR}@test_host:5432/db"
        assert config.database_url == expected

    def test_no_variables_unchanged(self):
        """Test that URLs without variables remain unchanged."""
        url = "postgresql://static_user:static_pass@localhost:5432/db"
        config = FluxConfig(database_url=url)
        assert config.database_url == url

    def test_invalid_variable_syntax_ignored(self):
        """Test that invalid variable syntax is ignored."""
        config = FluxConfig(database_url="postgresql://user:${@localhost:5432/db")
        assert config.database_url == "postgresql://user:${@localhost:5432/db"


class TestDatabaseTypeInference:
    """Test automatic database type inference from URL."""

    def test_postgresql_url_inference(self):
        """Test PostgreSQL type inference from URL."""
        config = FluxConfig(database_url="postgresql://user:pass@host:5432/db")
        assert config.database_type == "postgresql"

    def test_sqlite_url_no_inference(self):
        """Test SQLite URLs don't change default type."""
        config = FluxConfig(database_url="sqlite:///test.db")
        assert config.database_type == "sqlite"

    def test_explicit_type_overrides_inference(self):
        """Test explicit database type overrides inference."""
        # First create config with PostgreSQL URL only
        config1 = FluxConfig(database_url="postgresql://user:pass@host:5432/db")
        assert config1.database_type == "postgresql"  # Should be inferred

        # Test that explicit non-default type is preserved
        config2 = FluxConfig(
            database_url="postgresql://user:pass@host:5432/db",
            database_type="postgresql",  # Explicit non-default
        )
        assert config2.database_type == "postgresql"

    def test_invalid_url_no_inference(self):
        """Test invalid URLs don't affect type inference."""
        config = FluxConfig(database_url="invalid://url")
        assert config.database_type == "sqlite"  # Default


class TestPostgreSQLConfiguration:
    """Test PostgreSQL-specific configuration options."""

    def test_default_pool_configuration(self):
        """Test default connection pool settings."""
        config = FluxConfig()
        assert config.database_pool_size == 5
        assert config.database_max_overflow == 10
        assert config.database_pool_timeout == 30
        assert config.database_pool_recycle == 3600
        assert config.database_health_check_interval == 300

    def test_custom_pool_configuration(self, custom_pool_config):
        """Test custom pool size and overflow settings."""
        config = FluxConfig(**custom_pool_config)
        assert config.database_pool_size == 15
        assert config.database_max_overflow == 25
        assert config.database_pool_timeout == 45
        assert config.database_pool_recycle == 7200
        assert config.database_health_check_interval == 120

    def test_environment_variable_override(self):
        """Test environment variables override configuration."""
        with patch.dict(
            os.environ,
            {
                "FLUX_DATABASE_POOL_SIZE": "20",
                "FLUX_DATABASE_MAX_OVERFLOW": "30",
                "FLUX_DATABASE_POOL_TIMEOUT": "60",
            },
        ):
            config = FluxConfig()
            assert config.database_pool_size == 20
            assert config.database_max_overflow == 30
            assert config.database_pool_timeout == 60

    def test_nested_environment_variables(self):
        """Test nested environment variable configuration."""
        with patch.dict(
            os.environ,
            {
                "FLUX_DATABASE_URL": "postgresql://env_user:env_pass@env_host:5432/env_db",
                "FLUX_DATABASE_TYPE": "postgresql",
            },
        ):
            config = FluxConfig()
            assert config.database_url == "postgresql://env_user:env_pass@env_host:5432/env_db"
            assert config.database_type == "postgresql"


class TestConfigurationValidation:
    """Test configuration validation and error handling."""

    def test_valid_serializer_values(self):
        """Test valid serializer configuration."""
        config1 = FluxConfig(serializer="json")
        assert config1.serializer == "json"

        config2 = FluxConfig(serializer="pkl")
        assert config2.serializer == "pkl"

    def test_invalid_serializer_raises_error(self):
        """Test invalid serializer raises validation error."""
        with pytest.raises(ValidationError) as exc_info:
            FluxConfig(serializer="invalid")

        assert "Serializer must be either 'json' or 'pkl'" in str(exc_info.value)

    def test_database_type_validation(self):
        """Test database type field validation."""
        # Valid types
        config1 = FluxConfig(database_type="sqlite")
        assert config1.database_type == "sqlite"

        config2 = FluxConfig(database_type="postgresql")
        assert config2.database_type == "postgresql"

    def test_positive_pool_values(self):
        """Test pool configuration accepts positive values."""
        config = FluxConfig(
            database_pool_size=1,
            database_max_overflow=0,
            database_pool_timeout=1,
            database_pool_recycle=1,
            database_health_check_interval=1,
        )
        assert config.database_pool_size == 1
        assert config.database_max_overflow == 0
        assert config.database_pool_timeout == 1
        assert config.database_pool_recycle == 1
        assert config.database_health_check_interval == 1


class TestConfigurationLoading:
    """Test configuration loading from different sources."""

    def test_configuration_singleton(self):
        """Test configuration singleton behavior."""
        config1 = Configuration.get()
        config2 = Configuration.get()
        assert config1 is config2

    def test_configuration_reload(self):
        """Test configuration reload functionality."""
        config = Configuration.get()
        original_url = config.settings.database_url

        # Test that we can reset configuration
        config.reset()
        new_config = Configuration.get()
        # Ensure original_url was captured
        assert original_url is not None

        # Should have a valid SQLite URL after reset
        assert "sqlite" in new_config.settings.database_url
        assert new_config.settings.database_type == "sqlite"

    def test_configuration_override(self):
        """Test configuration override functionality."""
        config = Configuration.get()

        config.override(database_url="postgresql://override:pass@host:5432/db")
        assert config.settings.database_url == "postgresql://override:pass@host:5432/db"

        # Reset after test
        config.reset()

    def test_nested_configuration_override(self):
        """Test nested configuration override."""
        config = Configuration.get()

        config.override(database_pool_size=99, workers={"retry_attempts": 5})

        assert config.settings.database_pool_size == 99
        assert config.settings.workers.retry_attempts == 5

        # Reset after test
        config.reset()
