"""Database test fixtures for PostgreSQL and SQLite testing."""

import pytest
import tempfile
import os
from unittest.mock import patch, MagicMock
from sqlalchemy import create_engine
import sqlalchemy.exc
from flux.models import DatabaseRepository, RepositoryFactory, Base
from flux.config import Configuration, FluxConfig


@pytest.fixture
def temp_sqlite_db():
    """Create temporary SQLite database for testing."""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as tmp:
        db_path = tmp.name
    yield f"sqlite:///{db_path}"
    if os.path.exists(db_path):
        os.unlink(db_path)


def postgresql_available():
    """Check if PostgreSQL test database is available."""
    try:
        # Try to connect to the test database
        engine = create_engine(
            "postgresql://flux_test_user:flux_test_password@localhost:5433/flux_test",
            pool_pre_ping=True
        )
        with engine.connect():
            pass
        return True
    except Exception:
        return False


@pytest.fixture
def require_postgresql():
    """Skip test if PostgreSQL is not available."""
    if not postgresql_available():
        pytest.skip("PostgreSQL test database not available. Run 'make postgres-test-up' to start it.")


@pytest.fixture
def mock_postgresql_config():
    """Mock PostgreSQL configuration."""
    config = MagicMock(spec=FluxConfig)
    config.database_url = "postgresql://test_user:test_pass@localhost:5432/test_db"
    config.database_type = "postgresql"
    config.database_pool_size = 5
    config.database_max_overflow = 10
    config.database_pool_timeout = 30
    config.database_pool_recycle = 3600
    config.database_health_check_interval = 300
    config.debug = False
    
    with patch('flux.config.Configuration.get') as mock_get:
        mock_get.return_value.settings = config
        yield config


@pytest.fixture
def mock_sqlite_config():
    """Mock SQLite configuration."""
    config = MagicMock(spec=FluxConfig)
    config.database_url = "sqlite:///test.db"
    config.database_type = "sqlite"
    
    with patch('flux.config.Configuration.get') as mock_get:
        mock_get.return_value.settings = config
        yield config


@pytest.fixture
def mock_invalid_config():
    """Mock invalid database configuration."""
    config = MagicMock(spec=FluxConfig)
    config.database_url = "invalid://invalid"
    config.database_type = "invalid"
    
    with patch('flux.config.Configuration.get') as mock_get:
        mock_get.return_value.settings = config
        yield config


@pytest.fixture
def mock_postgres_engine():
    """Mock PostgreSQL engine for testing."""
    engine = MagicMock()
    connection = MagicMock()
    engine.connect.return_value.__enter__.return_value = connection
    connection.execute.return_value = None
    
    with patch('flux.models.create_engine', return_value=engine):
        yield engine


@pytest.fixture
def mock_failed_postgres_engine():
    """Mock PostgreSQL engine that fails connections."""
    engine = MagicMock()
    engine.connect.side_effect = Exception("Connection failed")
    
    with patch('flux.models.create_engine', return_value=engine):
        yield engine


@pytest.fixture
def isolated_postgres_repository(mock_postgresql_config, mock_postgres_engine):
    """Create isolated PostgreSQL repository for testing."""
    from flux.models import PostgreSQLRepository
    
    with patch.object(PostgreSQLRepository, '_validate_postgresql_url'):
        repo = PostgreSQLRepository()
        return repo


@pytest.fixture
def isolated_sqlite_repository(mock_sqlite_config):
    """Create isolated SQLite repository for testing."""
    from flux.models import SQLiteRepository
    
    with patch('flux.models.create_engine') as mock_create:
        mock_engine = MagicMock()
        mock_create.return_value = mock_engine
        repo = SQLiteRepository()
        return repo