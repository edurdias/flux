"""Tests for PostgreSQL-specific error handling."""

import pytest
from unittest.mock import patch, MagicMock
import sqlalchemy.exc

from flux.errors import DatabaseConnectionError, PostgreSQLConnectionError
from flux.models import PostgreSQLRepository

# Import fixtures


class TestDatabaseConnectionError:
    """Test base database connection error class."""

    def test_database_connection_error_creation(self):
        """Test DatabaseConnectionError creation with all parameters."""
        original_error = Exception("Original error")
        error = DatabaseConnectionError(
            message="Test message",
            database_type="postgresql",
            original_error=original_error,
        )

        assert str(error) == "Test message"
        assert error.database_type == "postgresql"
        assert error.original_error is original_error

    def test_database_connection_error_without_original(self):
        """Test DatabaseConnectionError creation without original error."""
        error = DatabaseConnectionError(message="Test message", database_type="sqlite")

        assert str(error) == "Test message"
        assert error.database_type == "sqlite"
        assert error.original_error is None


class TestPostgreSQLConnectionError:
    """Test PostgreSQL-specific connection error class."""

    def test_postgresql_connection_error_creation(self):
        """Test PostgreSQLConnectionError creation."""
        original_error = Exception("Original error")
        error = PostgreSQLConnectionError(message="PostgreSQL error", original_error=original_error)

        assert str(error) == "PostgreSQL error"
        assert error.database_type == "postgresql"
        assert error.original_error is original_error

    def test_postgresql_connection_error_inheritance(self):
        """Test PostgreSQLConnectionError inherits from DatabaseConnectionError."""
        error = PostgreSQLConnectionError("Test message")
        assert isinstance(error, DatabaseConnectionError)
        assert isinstance(error, PostgreSQLConnectionError)


class TestPostgreSQLRepositoryErrorHandling:
    """Test error handling in PostgreSQL repository."""

    def test_import_error_handling(self, mock_postgresql_config):
        """Test handling of missing PostgreSQL driver."""
        with patch("flux.models.create_engine") as mock_create:
            mock_create.side_effect = ImportError("No module named 'psycopg2'")

            with pytest.raises(PostgreSQLConnectionError) as exc_info:
                PostgreSQLRepository()

            error = exc_info.value
            assert "PostgreSQL driver not installed" in str(error)
            assert "pip install 'flux-core[postgresql]'" in str(error)
            assert error.database_type == "postgresql"
            assert isinstance(error.original_error, ImportError)

    def test_argument_error_handling(self, mock_postgresql_config):
        """Test handling of invalid URL format."""
        with patch("flux.models.create_engine") as mock_create:
            mock_create.side_effect = sqlalchemy.exc.ArgumentError("Invalid URL format")

            with pytest.raises(PostgreSQLConnectionError) as exc_info:
                PostgreSQLRepository()

            error = exc_info.value
            assert "Invalid PostgreSQL connection URL format" in str(error)
            assert isinstance(error.original_error, sqlalchemy.exc.ArgumentError)

    def test_operational_error_handling(self, mock_postgresql_config):
        """Test handling of database connection failures."""
        with patch("flux.models.create_engine") as mock_create:
            mock_engine = MagicMock()
            mock_create.return_value = mock_engine
            mock_engine.connect.side_effect = sqlalchemy.exc.OperationalError(
                "connection failed",
                None,
                None,
            )

            with pytest.raises(PostgreSQLConnectionError) as exc_info:
                PostgreSQLRepository()

            error = exc_info.value
            assert "Failed to connect to PostgreSQL database" in str(error)
            assert isinstance(error.original_error, sqlalchemy.exc.OperationalError)

    def test_unexpected_error_handling(self, mock_postgresql_config):
        """Test handling of unexpected errors."""
        with patch("flux.models.create_engine") as mock_create:
            mock_create.side_effect = RuntimeError("Unexpected error")

            with pytest.raises(PostgreSQLConnectionError) as exc_info:
                PostgreSQLRepository()

            error = exc_info.value
            assert "Unexpected error connecting to PostgreSQL" in str(error)
            assert isinstance(error.original_error, RuntimeError)

    def test_url_validation_errors(self):
        """Test URL validation error handling."""
        from flux.models import PostgreSQLRepository

        repo = PostgreSQLRepository.__new__(PostgreSQLRepository)

        # Test invalid scheme
        with pytest.raises(ValueError) as exc_info:
            repo._validate_postgresql_url("mysql://user:pass@host:3306/db")
        assert "PostgreSQL URL must start with 'postgresql://'" in str(exc_info.value)

        # Test missing hostname
        with pytest.raises(ValueError) as exc_info:
            repo._validate_postgresql_url("postgresql:///db")
        assert "PostgreSQL URL must include hostname" in str(exc_info.value)

    def test_engine_creation_success_with_validation(self, mock_postgresql_config):
        """Test successful engine creation with URL validation."""
        with patch("flux.models.create_engine") as mock_create:
            mock_engine = MagicMock()
            mock_connection = MagicMock()
            mock_engine.connect.return_value.__enter__.return_value = mock_connection
            mock_create.return_value = mock_engine

            # Should not raise any exceptions
            repo = PostgreSQLRepository()
            assert repo._engine is mock_engine

    def test_connection_test_failure(self, mock_postgresql_config):
        """Test connection test failure during engine creation."""
        with patch("flux.models.create_engine") as mock_create:
            mock_engine = MagicMock()
            mock_create.return_value = mock_engine

            # Mock connection context manager to raise exception
            mock_engine.connect.return_value.__enter__.side_effect = Exception(
                "Connection test failed",
            )

            with pytest.raises(PostgreSQLConnectionError) as exc_info:
                PostgreSQLRepository()

            error = exc_info.value
            assert "Unexpected error connecting to PostgreSQL" in str(error)

    def test_error_chaining_preserves_context(self, mock_postgresql_config):
        """Test that error chaining preserves original error context."""
        original_error = sqlalchemy.exc.OperationalError("Original message", None, None)

        with patch("flux.models.create_engine") as mock_create:
            mock_engine = MagicMock()
            mock_create.return_value = mock_engine
            mock_engine.connect.side_effect = original_error

            with pytest.raises(PostgreSQLConnectionError) as exc_info:
                PostgreSQLRepository()

            error = exc_info.value
            assert error.original_error is original_error
            assert "Failed to connect to PostgreSQL database" in str(error)
            assert "Original message" in str(error.original_error)


class TestErrorMessageQuality:
    """Test quality and usefulness of error messages."""

    def test_driver_installation_message_helpful(self, mock_postgresql_config):
        """Test driver installation error message is helpful."""
        with patch("flux.models.create_engine") as mock_create:
            mock_create.side_effect = ImportError("No module named 'psycopg2'")

            with pytest.raises(PostgreSQLConnectionError) as exc_info:
                PostgreSQLRepository()

            message = str(exc_info.value)
            assert "PostgreSQL driver not installed" in message
            assert "pip install 'flux-core[postgresql]'" in message
            # Should provide actionable solution

    def test_connection_failure_message_informative(self, mock_postgresql_config):
        """Test connection failure messages are informative."""
        with patch("flux.models.create_engine") as mock_create:
            mock_engine = MagicMock()
            mock_create.return_value = mock_engine
            mock_engine.connect.side_effect = sqlalchemy.exc.OperationalError(
                'FATAL: database "nonexistent" does not exist',
                None,
                None,
            )

            with pytest.raises(PostgreSQLConnectionError) as exc_info:
                PostgreSQLRepository()

            message = str(exc_info.value)
            assert "Failed to connect to PostgreSQL database" in message
            # Original error should be preserved for debugging
            assert exc_info.value.original_error is not None

    def test_url_validation_messages_specific(self):
        """Test URL validation error messages are specific."""
        from flux.models import PostgreSQLRepository

        repo = PostgreSQLRepository.__new__(PostgreSQLRepository)

        # Test specific error for wrong scheme
        with pytest.raises(ValueError) as exc_info:
            repo._validate_postgresql_url("sqlite:///test.db")
        assert "must start with 'postgresql://'" in str(exc_info.value)

        # Test specific error for missing hostname
        with pytest.raises(ValueError) as exc_info:
            repo._validate_postgresql_url("postgresql:///db")
        assert "must include hostname" in str(exc_info.value)
