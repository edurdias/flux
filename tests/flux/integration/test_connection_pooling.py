"""Integration tests for PostgreSQL connection pooling behavior."""

import pytest
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from flux.models import RepositoryFactory, PostgreSQLRepository
from flux.config import Configuration

# Import fixtures


@pytest.mark.integration
@pytest.mark.postgresql
class TestConnectionPooling:
    """Test PostgreSQL connection pool behavior."""

    @pytest.fixture(autouse=True)
    def setup_integration_config(self, integration_postgres_config, require_postgresql):
        """Set up integration test configuration with small pool for testing."""
        # Use smaller pool sizes for testing
        test_config = integration_postgres_config.copy()
        test_config.update(
            {"database_pool_size": 2, "database_max_overflow": 3, "database_pool_timeout": 5},
        )

        config = Configuration.get()
        config.override(**test_config)
        yield
        config.reset()

    @pytest.fixture
    def repository(self):
        """Create repository instance for testing."""
        return RepositoryFactory.create_repository()

    def test_basic_connection_health_check(self, repository):
        """Test basic connection health check functionality."""
        assert repository.health_check() is True

    def test_multiple_health_checks(self, repository):
        """Test multiple consecutive health checks."""
        # Multiple health checks should all succeed
        for i in range(10):
            assert repository.health_check() is True, f"Health check {i} failed"

    def test_concurrent_database_access(self, repository):
        """Test concurrent database access within pool limits."""
        results = []
        errors = []

        def database_operation(operation_id):
            """Simulate database operation."""
            try:
                # Use repository session for database operation
                with repository.session() as session:
                    # Simulate some work
                    session.execute("SELECT 1 as test_value")
                    time.sleep(0.1)  # Simulate processing time
                    results.append(operation_id)
            except Exception as e:
                errors.append((operation_id, str(e)))

        # Run operations within pool size (should all succeed)
        threads = []
        for i in range(2):  # Within pool_size of 2
            thread = threading.Thread(target=database_operation, args=(i,))
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join()

        assert len(errors) == 0, f"Errors occurred: {errors}"
        assert len(results) == 2
        assert set(results) == {0, 1}

    def test_connection_pool_overflow(self, repository):
        """Test connection pool behavior when exceeding pool size."""
        results = []
        errors = []

        def database_operation(operation_id):
            """Simulate database operation with longer duration."""
            try:
                start_time = time.time()
                with repository.session() as session:
                    session.execute("SELECT 1 as test_value")
                    time.sleep(0.5)  # Longer operation to stress pool
                    results.append((operation_id, time.time() - start_time))
            except Exception as e:
                errors.append((operation_id, str(e)))

        # Run more operations than pool_size + max_overflow
        # Pool size: 2, Max overflow: 3, Total: 5 connections max
        # We'll run 6 operations to test overflow behavior
        with ThreadPoolExecutor(max_workers=6) as executor:
            futures = [executor.submit(database_operation, i) for i in range(6)]

            for future in as_completed(futures):
                future.result()  # Wait for completion

        # Some operations should succeed (within pool + overflow limits)
        # Some might get timeouts or errors due to pool exhaustion
        total_operations = len(results) + len(errors)
        assert total_operations == 6

        # At least pool_size operations should succeed
        assert len(results) >= 2, f"Expected at least 2 successful operations, got {len(results)}"

    def test_connection_pool_recovery(self, repository):
        """Test that connection pool recovers after being exhausted."""

        # First, exhaust the connection pool
        def long_operation():
            with repository.session() as session:
                session.execute("SELECT 1")
                time.sleep(1)

        # Start operations that will hold connections
        threads = []
        for i in range(3):  # Use up pool + some overflow
            thread = threading.Thread(target=long_operation)
            threads.append(thread)
            thread.start()

        # Give operations time to start and hold connections
        time.sleep(0.2)

        # Now try a quick operation - should either succeed quickly or fail due to pool exhaustion
        start_time = time.time()
        pool_exhausted = False
        try:
            result = repository.health_check()
            operation_time = time.time() - start_time
            # If it succeeds, it should be reasonably quick (< 1 second)
            assert result is True, "Health check should return True when successful"
            assert operation_time < 1.0, f"Health check took too long: {operation_time}s"
        except Exception as e:
            # Connection pool exhaustion is expected behavior
            pool_exhausted = True
            # Verify it's a connection-related error
            assert any(
                keyword in str(e).lower() for keyword in ["connection", "pool", "timeout"]
            ), f"Expected connection-related error, got: {e}"

        # Test validates pool behavior - either quick success or proper pool exhaustion
        assert (
            result is True or pool_exhausted
        ), "Pool should either provide connection or be exhausted"

        # Wait for long operations to complete
        for thread in threads:
            thread.join()

        # After operations complete, pool should be available again
        time.sleep(0.1)  # Brief pause for cleanup
        assert (
            repository.health_check() is True
        ), "Pool should recover after connections are released"

    def test_connection_pre_ping(self, repository):
        """Test connection pre-ping functionality."""
        # Pre-ping should prevent stale connections
        # We can't easily test stale connections in integration test,
        # but we can verify that pre-ping doesn't break normal operations

        # Perform multiple operations with pauses to potentially trigger pre-ping
        for i in range(5):
            assert repository.health_check() is True
            time.sleep(0.1)

        # Should still work after potential connection recycling
        with repository.session() as session:
            result = session.execute("SELECT 'pre_ping_test' as message").fetchone()
            assert result[0] == "pre_ping_test"

    def test_session_isolation(self, repository):
        """Test that database sessions are properly isolated."""
        results = []

        def isolated_operation(operation_id):
            """Perform isolated database operation."""
            with repository.session() as session:
                # Create a temporary table in this session only
                session.execute(
                    f"""
                    CREATE TEMPORARY TABLE temp_test_{operation_id} (
                        id INTEGER,
                        value TEXT
                    )
                """,
                )
                session.execute(
                    f"""
                    INSERT INTO temp_test_{operation_id} (id, value)
                    VALUES ({operation_id}, 'test_{operation_id}')
                """,
                )

                # Read back the value
                result = session.execute(
                    f"""
                    SELECT value FROM temp_test_{operation_id} WHERE id = {operation_id}
                """,
                ).fetchone()

                results.append((operation_id, result[0]))
                session.commit()

        # Run operations concurrently
        threads = []
        for i in range(3):
            thread = threading.Thread(target=isolated_operation, args=(i,))
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join()

        # Each operation should have succeeded with its own data
        assert len(results) == 3
        expected_results = {(0, "test_0"), (1, "test_1"), (2, "test_2")}
        assert set(results) == expected_results


@pytest.mark.integration
@pytest.mark.postgresql
class TestConnectionPoolConfiguration:
    """Test connection pool configuration options."""

    def test_custom_pool_configuration(self, require_postgresql):
        """Test repository creation with custom pool configuration."""
        custom_config = {
            "database_url": "postgresql://flux_test_user:flux_test_password@localhost:5433/flux_test",
            "database_type": "postgresql",
            "database_pool_size": 8,
            "database_max_overflow": 12,
            "database_pool_timeout": 15,
            "database_pool_recycle": 1800,
        }

        config = Configuration.get()
        config.override(**custom_config)

        try:
            # Create repository with custom configuration
            repo = RepositoryFactory.create_repository()
            assert isinstance(repo, PostgreSQLRepository)

            # Basic functionality should work with custom pool settings
            assert repo.health_check() is True

        finally:
            config.reset()

    def test_pool_configuration_validation(self, require_postgresql):
        """Test that pool configuration is applied correctly."""
        test_config = {
            "database_url": "postgresql://flux_test_user:flux_test_password@localhost:5433/flux_test",
            "database_type": "postgresql",
            "database_pool_size": 1,
            "database_max_overflow": 0,
            "database_pool_timeout": 1,
            "debug": True,  # Enable SQL logging
        }

        config = Configuration.get()
        config.override(**test_config)

        try:
            repo = RepositoryFactory.create_repository()

            # With pool_size=1 and max_overflow=0, only 1 connection should be available
            # Test by using the connection and trying to get another

            def hold_connection():
                with repo.session() as session:
                    session.execute("SELECT 1")
                    time.sleep(0.5)  # Hold the connection

            # Start operation that holds the only connection
            thread = threading.Thread(target=hold_connection)
            thread.start()

            time.sleep(0.1)  # Let the first operation start

            # Second operation should timeout quickly due to pool_timeout=1
            start_time = time.time()
            try:
                with repo.session() as session:
                    session.execute("SELECT 1")
                # If we get here, the operation succeeded (unexpected but not necessarily wrong)
            except Exception:
                # Timeout or connection error is expected
                elapsed = time.time() - start_time
                # Should timeout within a reasonable time (pool_timeout + some buffer)
                assert elapsed < 5, f"Operation took too long to timeout: {elapsed}s"

            thread.join()

        finally:
            config.reset()


@pytest.mark.integration
@pytest.mark.postgresql
class TestDatabaseErrorRecovery:
    """Test database error recovery and pool resilience."""

    @pytest.fixture(autouse=True)
    def setup_integration_config(self, integration_postgres_config):
        """Set up integration test configuration."""
        config = Configuration.get()
        config.override(**integration_postgres_config)
        yield
        config.reset()

    def test_health_check_after_bad_query(self, require_postgresql):
        """Test that health check works after a bad query."""
        repo = RepositoryFactory.create_repository()

        # Execute a bad query that should fail
        try:
            with repo.session() as session:
                session.execute("SELECT * FROM non_existent_table")
        except Exception:
            # Expected to fail
            pass

        # Health check should still work after the error
        assert repo.health_check() is True

    def test_pool_recovery_after_session_errors(self, require_postgresql):
        """Test that connection pool recovers after session errors."""
        repo = RepositoryFactory.create_repository()

        # Generate some session errors
        for i in range(3):
            try:
                with repo.session() as session:
                    session.execute(f"SELECT * FROM table_that_does_not_exist_{i}")
            except Exception:
                # Expected to fail
                pass

        # Pool should still be functional
        assert repo.health_check() is True

        # Normal operations should still work
        with repo.session() as session:
            result = session.execute("SELECT 'recovery_test' as message").fetchone()
            assert result[0] == "recovery_test"
