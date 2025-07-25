"""Tests for database repository factory and PostgreSQL repository implementation."""

import pytest
from unittest.mock import patch, MagicMock
import sqlalchemy.exc

from flux.models import RepositoryFactory, SQLiteRepository, PostgreSQLRepository
from flux.errors import PostgreSQLConnectionError

# Import fixtures


class TestRepositoryFactory:
    """Test repository factory pattern."""

    def test_create_sqlite_repository(self, mock_sqlite_config):
        """Test SQLite repository creation."""
        repo = RepositoryFactory.create_repository()
        assert isinstance(repo, SQLiteRepository)

    def test_create_postgresql_repository(self, mock_postgresql_config):
        """Test PostgreSQL repository creation."""
        with patch("flux.models.PostgreSQLRepository._validate_postgresql_url"):
            with patch("flux.models.create_engine"):
                repo = RepositoryFactory.create_repository()
                assert isinstance(repo, PostgreSQLRepository)

    def test_unsupported_database_type_raises_error(self, mock_invalid_config):
        """Test error handling for invalid database types."""
        with pytest.raises(ValueError, match="Unsupported database type: invalid"):
            RepositoryFactory.create_repository()

    def test_factory_respects_configuration_changes(self):
        """Test that factory responds to configuration changes."""
        # Test SQLite
        with patch("flux.config.Configuration.get") as mock_get:
            mock_config = MagicMock()
            mock_config.database_type = "sqlite"
            mock_get.return_value.settings = mock_config

            with patch("flux.models.create_engine"):
                repo1 = RepositoryFactory.create_repository()
                assert isinstance(repo1, SQLiteRepository)

        # Test PostgreSQL
        with patch("flux.config.Configuration.get") as mock_get:
            mock_config = MagicMock()
            mock_config.database_type = "postgresql"
            mock_get.return_value.settings = mock_config

            with patch("flux.models.PostgreSQLRepository._validate_postgresql_url"):
                with patch("flux.models.create_engine"):
                    repo2 = RepositoryFactory.create_repository()
                    assert isinstance(repo2, PostgreSQLRepository)


class TestPostgreSQLRepository:
    """Test PostgreSQL repository implementation."""

    def test_engine_creation_with_valid_config(self, mock_postgresql_config):
        """Test engine creation with valid PostgreSQL configuration."""
        with patch("flux.models.create_engine") as mock_create:
            with patch("flux.models.PostgreSQLRepository._validate_postgresql_url"):
                mock_engine = MagicMock()
                mock_connection = MagicMock()
                mock_engine.connect.return_value.__enter__.return_value = mock_connection
                mock_create.return_value = mock_engine

                repo = PostgreSQLRepository()
                assert repo is not None

                # Verify create_engine was called with correct arguments
                mock_create.assert_called_once()
                args, kwargs = mock_create.call_args

                assert args[0] == "postgresql://test_user:test_pass@localhost:5432/test_db"
                assert kwargs["pool_size"] == 5
                assert kwargs["max_overflow"] == 10
                assert kwargs["pool_timeout"] == 30
                assert kwargs["pool_recycle"] == 3600
                assert kwargs["pool_pre_ping"] is True
                assert kwargs["echo"] is False

    def test_connection_pool_configuration(self, mock_postgresql_config):
        """Test connection pool parameters are applied correctly."""
        # Modify config for custom pool settings
        mock_postgresql_config.database_pool_size = 15
        mock_postgresql_config.database_max_overflow = 25
        mock_postgresql_config.database_pool_timeout = 45
        mock_postgresql_config.database_pool_recycle = 7200
        mock_postgresql_config.debug = True

        with patch("flux.models.create_engine") as mock_create:
            with patch("flux.models.PostgreSQLRepository._validate_postgresql_url"):
                mock_engine = MagicMock()
                mock_connection = MagicMock()
                mock_engine.connect.return_value.__enter__.return_value = mock_connection
                mock_create.return_value = mock_engine

                PostgreSQLRepository()

                args, kwargs = mock_create.call_args
                assert kwargs["pool_size"] == 15
                assert kwargs["max_overflow"] == 25
                assert kwargs["pool_timeout"] == 45
                assert kwargs["pool_recycle"] == 7200
                assert kwargs["echo"] is True  # Debug mode enabled

    def test_health_check_success(self, isolated_postgres_repository):
        """Test successful health check."""
        with patch.object(isolated_postgres_repository, "session") as mock_session:
            mock_session_instance = MagicMock()
            mock_session.return_value.__enter__.return_value = mock_session_instance
            mock_session_instance.execute.return_value = None

            result = isolated_postgres_repository.health_check()
            assert result is True
            mock_session_instance.execute.assert_called_once()

    def test_health_check_failure(self, isolated_postgres_repository):
        """Test health check failure handling."""
        with patch.object(isolated_postgres_repository, "session") as mock_session:
            mock_session.side_effect = Exception("Database connection failed")

            result = isolated_postgres_repository.health_check()
            assert result is False

    def test_url_validation_valid_postgresql_url(self):
        """Test validation of valid PostgreSQL URLs."""
        from flux.models import PostgreSQLRepository

        repo = PostgreSQLRepository.__new__(PostgreSQLRepository)

        # These should not raise exceptions
        repo._validate_postgresql_url("postgresql://user:pass@localhost:5432/db")
        repo._validate_postgresql_url("postgresql://user@localhost/db")
        repo._validate_postgresql_url("postgresql://localhost:5432/db")

    def test_url_validation_invalid_postgresql_url(self):
        """Test validation of invalid PostgreSQL URLs."""
        from flux.models import PostgreSQLRepository

        repo = PostgreSQLRepository.__new__(PostgreSQLRepository)

        # Invalid scheme
        with pytest.raises(ValueError, match="PostgreSQL URL must start with 'postgresql://'"):
            repo._validate_postgresql_url("mysql://user:pass@localhost:3306/db")

        # Missing hostname
        with pytest.raises(ValueError, match="PostgreSQL URL must include hostname"):
            repo._validate_postgresql_url("postgresql:///db")

    def test_missing_driver_error(self, mock_postgresql_config):
        """Test error when PostgreSQL driver is not installed."""
        with patch("flux.models.create_engine") as mock_create:
            mock_create.side_effect = ImportError("No module named 'psycopg2'")

            with pytest.raises(PostgreSQLConnectionError) as exc_info:
                PostgreSQLRepository()

            assert "PostgreSQL driver not installed" in str(exc_info.value)
            assert "pip install 'flux-core[postgresql]'" in str(exc_info.value)
            assert isinstance(exc_info.value.original_error, ImportError)

    def test_invalid_url_format_error(self, mock_postgresql_config):
        """Test error for invalid URL format."""
        with patch("flux.models.create_engine") as mock_create:
            mock_create.side_effect = sqlalchemy.exc.ArgumentError("Invalid URL format")

            with pytest.raises(PostgreSQLConnectionError) as exc_info:
                PostgreSQLRepository()

            assert "Invalid PostgreSQL connection URL format" in str(exc_info.value)
            assert isinstance(exc_info.value.original_error, sqlalchemy.exc.ArgumentError)

    def test_operational_error(self, mock_postgresql_config):
        """Test operational error handling."""
        with patch("flux.models.create_engine") as mock_create:
            mock_engine = MagicMock()
            mock_create.return_value = mock_engine
            mock_engine.connect.side_effect = sqlalchemy.exc.OperationalError(
                'connection to server at "localhost" (127.0.0.1), port 5432 failed',
                None,
                None,
            )

            with pytest.raises(PostgreSQLConnectionError) as exc_info:
                PostgreSQLRepository()

            assert "Failed to connect to PostgreSQL database" in str(exc_info.value)
            assert isinstance(exc_info.value.original_error, sqlalchemy.exc.OperationalError)

    def test_unexpected_error(self, mock_postgresql_config):
        """Test unexpected error handling."""
        with patch("flux.models.create_engine") as mock_create:
            mock_create.side_effect = RuntimeError("Unexpected error")

            with pytest.raises(PostgreSQLConnectionError) as exc_info:
                PostgreSQLRepository()

            assert "Unexpected error connecting to PostgreSQL" in str(exc_info.value)
            assert isinstance(exc_info.value.original_error, RuntimeError)


class TestSQLiteRepository:
    """Test SQLite repository implementation."""

    def test_engine_creation_with_sqlite_config(self, mock_sqlite_config):
        """Test SQLite engine creation."""
        with patch("flux.models.create_engine") as mock_create:
            mock_engine = MagicMock()
            mock_create.return_value = mock_engine

            repo = SQLiteRepository()
            assert repo is not None

            mock_create.assert_called_once_with("sqlite:///test.db")

    def test_health_check_sqlite(self, isolated_sqlite_repository):
        """Test SQLite health check."""
        with patch.object(isolated_sqlite_repository, "session") as mock_session:
            mock_session_instance = MagicMock()
            mock_session.return_value.__enter__.return_value = mock_session_instance
            mock_session_instance.execute.return_value = None

            result = isolated_sqlite_repository.health_check()
            assert result is True
