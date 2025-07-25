"""Configuration test fixtures for PostgreSQL testing."""

import pytest
import os
from unittest.mock import patch


@pytest.fixture
def mock_env_vars():
    """Mock environment variables for testing."""
    env_vars = {
        'DB_USER': 'test_user',
        'DB_PASSWORD': 'test_password',
        'DB_HOST': 'test_host',
        'DB_PORT': '5432',
        'DB_NAME': 'test_database',
        'POSTGRES_USER': 'postgres_user',
        'POSTGRES_PASSWORD': 'postgres_password'
    }
    
    with patch.dict(os.environ, env_vars):
        yield env_vars


@pytest.fixture
def sample_database_urls():
    """Sample database URLs for testing."""
    return {
        'sqlite_simple': 'sqlite:///test.db',
        'sqlite_memory': 'sqlite:///:memory:',
        'postgresql_simple': 'postgresql://user:pass@host:5432/db',
        'postgresql_with_env': 'postgresql://${DB_USER}:${DB_PASSWORD}@${DB_HOST}:${DB_PORT}/${DB_NAME}',
        'postgresql_mixed': 'postgresql://static_user:${DB_PASSWORD}@localhost:5432/flux_db',
        'postgresql_minimal': 'postgresql://localhost/db',
        'invalid_scheme': 'mysql://user:pass@host:3306/db'
    }


@pytest.fixture
def integration_postgres_config():
    """Configuration for integration tests with real PostgreSQL."""
    return {
        'database_url': 'postgresql://flux_test_user:flux_test_password@localhost:5433/flux_test',
        'database_type': 'postgresql',
        'database_pool_size': 2,
        'database_max_overflow': 3,
        'database_pool_timeout': 10,
        'database_pool_recycle': 1800,
        'database_health_check_interval': 60
    }


@pytest.fixture
def custom_pool_config():
    """Custom connection pool configuration for testing."""
    return {
        'database_pool_size': 15,
        'database_max_overflow': 25,
        'database_pool_timeout': 45,
        'database_pool_recycle': 7200,
        'database_health_check_interval': 120
    }


@pytest.fixture  
def missing_env_vars():
    """Test environment with missing variables."""
    # Ensure these vars are not set
    vars_to_remove = ['DB_USER', 'DB_PASSWORD', 'DB_HOST', 'DB_NAME']
    original_values = {}
    
    for var in vars_to_remove:
        if var in os.environ:
            original_values[var] = os.environ[var]
            del os.environ[var]
    
    yield vars_to_remove
    
    # Restore original values
    for var, value in original_values.items():
        os.environ[var] = value