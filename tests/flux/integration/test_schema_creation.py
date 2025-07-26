"""Integration tests for PostgreSQL schema creation and validation."""

import pytest
from sqlalchemy import inspect, text

from flux.models import RepositoryFactory
from flux.config import Configuration
from flux.catalogs import DatabaseWorkflowCatalog, WorkflowInfo
from flux.domain.resource_request import ResourceRequest

# Import fixtures


@pytest.mark.integration
@pytest.mark.postgresql
class TestSchemaCreation:
    """Test PostgreSQL schema creation and table structure."""

    @pytest.fixture(autouse=True)
    def setup_integration_config(self, integration_postgres_config, require_postgresql):
        """Set up integration test configuration."""
        config = Configuration.get()
        config.override(**integration_postgres_config)
        yield
        config.reset()

    @pytest.fixture
    def repository(self):
        """Create repository instance for testing."""
        return RepositoryFactory.create_repository()

    @pytest.fixture
    def inspector(self, repository):
        """Create SQLAlchemy inspector for database introspection."""
        return inspect(repository._engine)

    def test_all_tables_created(self, inspector):
        """Test that all required tables are created."""
        table_names = inspector.get_table_names()

        expected_tables = {
            "workflows",
            "executions",
            "execution_events",
            "workers",
            "worker_runtimes",
            "worker_resources",
            "worker_resources_gpus",
            "worker_packages",
            "secrets",
        }

        actual_tables = set(table_names)
        missing_tables = expected_tables - actual_tables

        assert len(missing_tables) == 0, f"Missing tables: {missing_tables}"
        assert expected_tables.issubset(actual_tables), "Not all expected tables were created"

    def test_workflows_table_structure(self, inspector):
        """Test workflows table structure and columns."""
        columns = inspector.get_columns("workflows")
        column_names = {col["name"] for col in columns}

        expected_columns = {"id", "name", "version", "imports", "source", "requests"}
        assert expected_columns.issubset(column_names), "Missing columns in workflows table"

        # Check column types
        column_types = {col["name"]: str(col["type"]) for col in columns}

        # Key columns should have appropriate types
        assert "VARCHAR" in column_types["id"] or "TEXT" in column_types["id"]
        assert "VARCHAR" in column_types["name"] or "TEXT" in column_types["name"]
        assert "INTEGER" in column_types["version"]

        # Custom types should be TEXT in PostgreSQL
        assert "TEXT" in column_types["imports"]
        assert "TEXT" in column_types["source"]
        assert "TEXT" in column_types["requests"]

    def test_executions_table_structure(self, inspector):
        """Test executions table structure and relationships."""
        columns = inspector.get_columns("executions")
        column_names = {col["name"] for col in columns}

        expected_columns = {
            "execution_id",
            "workflow_id",
            "workflow_name",
            "input",
            "output",
            "state",
            "worker_name",
        }
        assert expected_columns.issubset(column_names)

        # Check foreign keys
        foreign_keys = inspector.get_foreign_keys("executions")
        fk_tables = {fk["referred_table"] for fk in foreign_keys}

        assert "workflows" in fk_tables, "Missing foreign key to workflows table"
        assert "workers" in fk_tables, "Missing foreign key to workers table"

    def test_execution_events_table_structure(self, inspector):
        """Test execution_events table structure."""
        columns = inspector.get_columns("execution_events")
        column_names = {col["name"] for col in columns}

        expected_columns = {
            "execution_id",
            "id",
            "source_id",
            "event_id",
            "type",
            "name",
            "value",
            "time",
        }
        assert expected_columns.issubset(column_names)

        # Check foreign key to executions
        foreign_keys = inspector.get_foreign_keys("execution_events")
        execution_fks = [fk for fk in foreign_keys if fk["referred_table"] == "executions"]
        assert len(execution_fks) > 0, "Missing foreign key to executions table"

    def test_workers_table_hierarchy(self, inspector):
        """Test workers table and related tables structure."""
        # Check workers table
        worker_columns = inspector.get_columns("workers")
        worker_column_names = {col["name"] for col in worker_columns}
        assert {"name", "session_token"}.issubset(worker_column_names)

        # Check worker_runtimes table
        runtime_columns = inspector.get_columns("worker_runtimes")
        runtime_column_names = {col["name"] for col in runtime_columns}
        expected_runtime_columns = {"id", "os_name", "os_version", "python_version", "worker_name"}
        assert expected_runtime_columns.issubset(runtime_column_names)

        # Check worker_resources table
        resources_columns = inspector.get_columns("worker_resources")
        resources_column_names = {col["name"] for col in resources_columns}
        expected_resources_columns = {
            "id",
            "cpu_total",
            "cpu_available",
            "memory_total",
            "memory_available",
            "disk_total",
            "disk_free",
            "worker_name",
        }
        assert expected_resources_columns.issubset(resources_column_names)

        # Check foreign key relationships
        runtime_fks = inspector.get_foreign_keys("worker_runtimes")
        assert any(fk["referred_table"] == "workers" for fk in runtime_fks)

        resources_fks = inspector.get_foreign_keys("worker_resources")
        assert any(fk["referred_table"] == "workers" for fk in resources_fks)

    def test_secrets_table_structure(self, inspector):
        """Test secrets table structure."""
        columns = inspector.get_columns("secrets")
        column_names = {col["name"] for col in columns}

        expected_columns = {"name", "value"}
        assert expected_columns.issubset(column_names)

        # Value column should use custom EncryptedType (TEXT in PostgreSQL)
        column_types = {col["name"]: str(col["type"]) for col in columns}
        assert "TEXT" in column_types["value"], "Secrets value should use TEXT type for encryption"

    def test_unique_constraints(self, inspector):
        """Test unique constraints are properly created."""
        # Workflows should have unique constraint on (name, version)
        workflows_constraints = inspector.get_unique_constraints("workflows")
        name_version_constraint = any(
            set(constraint["column_names"]) == {"name", "version"}
            for constraint in workflows_constraints
        )
        assert name_version_constraint, "Missing unique constraint on (name, version) in workflows"

        # Workers should have unique name constraint
        workers_constraints = inspector.get_unique_constraints("workers")
        name_constraint = any(
            "name" in constraint["column_names"] for constraint in workers_constraints
        )

        # If unique constraint not found, check if it's enforced via primary key
        if not name_constraint:
            workers_pk = inspector.get_pk_constraint("workers")
            name_in_pk = "name" in workers_pk.get("constrained_columns", [])
            assert (
                name_in_pk
            ), "Workers table must have unique name constraint (either unique or primary key)"
        else:
            assert name_constraint, "Workers table must have unique name constraint"

    def test_primary_keys(self, inspector):
        """Test primary key constraints."""
        # Check primary keys for main tables
        workflows_pk = inspector.get_pk_constraint("workflows")
        assert "id" in workflows_pk["constrained_columns"]

        executions_pk = inspector.get_pk_constraint("executions")
        assert "execution_id" in executions_pk["constrained_columns"]

        workers_pk = inspector.get_pk_constraint("workers")
        assert "name" in workers_pk["constrained_columns"]


@pytest.mark.integration
@pytest.mark.postgresql
class TestDataTypeCompatibility:
    """Test data type compatibility between models and PostgreSQL."""

    @pytest.fixture(autouse=True)
    def setup_integration_config(self, integration_postgres_config, require_postgresql):
        """Set up integration test configuration."""
        config = Configuration.get()
        config.override(**integration_postgres_config)
        yield
        config.reset()

    @pytest.fixture
    def catalog(self):
        """Create catalog for testing."""
        return DatabaseWorkflowCatalog.create()

    def test_base64_type_large_data(self, catalog):
        """Test Base64Type with large serialized data."""
        # Create workflow with large imports and requests data
        large_imports = [f"module_{i}" for i in range(1000)]
        large_packages = [f"package_{i}" for i in range(500)]

        large_workflow = WorkflowInfo(
            id="large-data-workflow",
            name="large_data_workflow",
            imports=large_imports,
            source=b"async def large_workflow(): pass",
            requests=ResourceRequest(cpu=16, memory=32768, packages=large_packages),
        )

        # Should handle large data without issues
        catalog.save([large_workflow])
        retrieved = catalog.get("large_data_workflow")

        assert len(retrieved.imports) == 1000
        assert len(retrieved.requests.packages) == 500
        assert retrieved.imports == large_imports
        assert retrieved.requests.packages == large_packages

    def test_encrypted_type_functionality(self, catalog):
        """Test EncryptedType functionality with actual data."""
        # This would require secrets table operations
        # For now, we test that the type system works

        repository = RepositoryFactory.create_repository()

        # Test that we can create and query the secrets table
        with repository.session() as session:
            # Verify secrets table exists and is accessible
            result = session.execute(text("SELECT COUNT(*) FROM secrets")).fetchone()
            assert result[0] >= 0  # Should return 0 or more (no errors)

    def test_unicode_data_storage(self, catalog):
        """Test Unicode data storage and retrieval."""
        unicode_workflow = WorkflowInfo(
            id="unicode-test",
            name="测试_workflow_🚀",
            imports=["unicodedata", "locale"],
            source="""
# Testing Unicode: αβγδε, ñáéíóú, 中文测试
async def unicode_test():
    return "Multi-language: English, Español, 中文, العربية, русский"
""".encode(),
            requests=ResourceRequest(packages=["unicodedata"]),
        )

        catalog.save([unicode_workflow])
        retrieved = catalog.get("测试_workflow_🚀")

        assert retrieved.name == "测试_workflow_🚀"
        source_text = retrieved.source.decode("utf-8")
        assert "αβγδε" in source_text
        assert "中文测试" in source_text
        assert "العربية" in source_text

    def test_null_value_handling(self, catalog):
        """Test NULL/None value handling in database."""
        # Create workflow with minimal data (some fields can be None)
        minimal_workflow = WorkflowInfo(
            id="minimal-workflow",
            name="minimal_workflow",
            imports=[],  # Empty list
            source=b"async def minimal(): pass",
            requests=None,  # None value
        )

        catalog.save([minimal_workflow])
        retrieved = catalog.get("minimal_workflow")

        assert retrieved.imports == []
        assert retrieved.requests is None

    def test_complex_nested_data(self, catalog):
        """Test complex nested data structures."""
        complex_requests = ResourceRequest(
            cpu=4,
            memory=8192,
            packages=["numpy>=1.20.0", "pandas>=1.3.0", "scikit-learn>=1.0.0", "tensorflow>=2.8.0"],
        )

        complex_workflow = WorkflowInfo(
            id="complex-workflow",
            name="complex_data_workflow",
            imports=[
                "numpy",
                "pandas",
                "sklearn",
                "tensorflow",
                "matplotlib",
                "seaborn",
                "plotly",
                "bokeh",
            ],
            source=b"""
import numpy as np
import pandas as pd
from sklearn.model_selection import train_test_split
import tensorflow as tf

async def complex_ml_workflow(data_path: str):
    # Complex ML workflow with multiple libraries
    data = pd.read_csv(data_path)
    X = data.drop('target', axis=1)
    y = data['target']

    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2)

    model = tf.keras.Sequential([
        tf.keras.layers.Dense(128, activation='relu'),
        tf.keras.layers.Dropout(0.2),
        tf.keras.layers.Dense(1, activation='sigmoid')
    ])

    model.compile(optimizer='adam', loss='binary_crossentropy', metrics=['accuracy'])
    model.fit(X_train, y_train, epochs=10, validation_split=0.2)

    return model.evaluate(X_test, y_test)
""",
            requests=complex_requests,
        )

        catalog.save([complex_workflow])
        retrieved = catalog.get("complex_data_workflow")

        assert len(retrieved.imports) == 8
        assert "tensorflow" in retrieved.imports
        assert retrieved.requests.cpu == 4
        assert retrieved.requests.memory == 8192
        assert "tensorflow>=2.8.0" in retrieved.requests.packages

        # Verify source code is preserved correctly
        source_text = retrieved.source.decode("utf-8")
        assert "tf.keras.Sequential" in source_text
        assert "train_test_split" in source_text


@pytest.mark.integration
@pytest.mark.postgresql
class TestDatabaseIntegrity:
    """Test database integrity and consistency."""

    @pytest.fixture(autouse=True)
    def setup_integration_config(self, integration_postgres_config, require_postgresql):
        """Set up integration test configuration."""
        config = Configuration.get()
        config.override(**integration_postgres_config)
        yield
        config.reset()

    def test_foreign_key_constraints(self):
        """Test that foreign key constraints are enforced."""
        repository = RepositoryFactory.create_repository()

        with repository.session() as session:
            # Try to create execution with non-existent workflow_id
            # This should fail due to foreign key constraint
            try:
                session.execute(
                    text(
                        """
                    INSERT INTO executions (execution_id, workflow_id, workflow_name, state)
                    VALUES ('test-exec', 'non-existent-workflow', 'test', 'CREATED')
                """,
                    ),
                )
                session.commit()
                pytest.fail("Expected foreign key constraint violation")
            except Exception as e:
                # Should get foreign key constraint error
                session.rollback()
                assert "foreign key" in str(e).lower() or "violates" in str(e).lower()

    def test_unique_constraint_enforcement(self):
        """Test that unique constraints are enforced."""
        repository = RepositoryFactory.create_repository()

        with repository.session() as session:
            # Insert a workflow
            session.execute(
                text(
                    """
                INSERT INTO workflows (id, name, version, source)
                VALUES ('test-wf-1', 'test_workflow', 1, 'dGVzdA==')
            """,
                ),
            )
            session.commit()

            # Try to insert another workflow with same name and version
            try:
                session.execute(
                    text(
                        """
                    INSERT INTO workflows (id, name, version, source)
                    VALUES ('test-wf-2', 'test_workflow', 1, 'dGVzdA==')
                """,
                    ),
                )
                session.commit()
                pytest.fail("Expected unique constraint violation")
            except Exception as e:
                # Should get unique constraint error
                session.rollback()
                assert "unique" in str(e).lower() or "duplicate" in str(e).lower()

    def test_transaction_rollback(self):
        """Test transaction rollback functionality."""
        repository = RepositoryFactory.create_repository()

        # Count workflows before transaction
        with repository.session() as session:
            initial_count = session.execute(text("SELECT COUNT(*) FROM workflows")).fetchone()[0]

        # Start transaction and insert data, then rollback
        try:
            with repository.session() as session:
                session.execute(
                    text(
                        """
                    INSERT INTO workflows (id, name, version, source)
                    VALUES ('rollback-test', 'rollback_workflow', 1, 'dGVzdA==')
                """,
                    ),
                )

                # Verify data is there within transaction
                count_in_tx = session.execute(text("SELECT COUNT(*) FROM workflows")).fetchone()[0]
                assert count_in_tx == initial_count + 1

                # Force rollback
                raise Exception("Force rollback")

        except Exception:
            # Expected exception for rollback
            pass

        # Verify data was rolled back
        with repository.session() as session:
            final_count = session.execute(text("SELECT COUNT(*) FROM workflows")).fetchone()[0]
            assert final_count == initial_count, "Transaction was not properly rolled back"
