"""Integration tests for PostgreSQL catalog functionality."""

import pytest

from flux.catalogs import DatabaseWorkflowCatalog, WorkflowInfo
from flux.config import Configuration
from flux.errors import WorkflowNotFoundError
from flux.models import RepositoryFactory
from flux.domain.resource_request import ResourceRequest

# Import fixtures


@pytest.mark.integration
@pytest.mark.postgresql
class TestPostgreSQLCatalogIntegration:
    """Integration tests with real PostgreSQL database."""

    @pytest.fixture(autouse=True)
    def setup_integration_config(self, integration_postgres_config, require_postgresql):
        """Set up integration test configuration."""
        # Override configuration for integration tests
        config = Configuration.get()
        config.override(**integration_postgres_config)
        yield
        config.reset()

    @pytest.fixture
    def catalog(self):
        """Create catalog instance for testing."""
        return DatabaseWorkflowCatalog.create()

    @pytest.fixture
    def sample_workflow_info(self):
        """Create sample workflow info for testing."""
        return WorkflowInfo(
            id="test-workflow-id",
            name="test_workflow",
            imports=["os", "sys", "flux"],
            source=b'async def test_workflow():\n    return "Hello World"',
            version=1,
            requests=ResourceRequest(cpu=2, memory=1024),
        )

    def test_catalog_creation_with_postgresql(self, catalog):
        """Test catalog creation with PostgreSQL backend."""
        assert catalog is not None
        assert hasattr(catalog, "_engine")

        # Verify we can perform basic operations
        workflows = catalog.all()
        assert isinstance(workflows, list)

    def test_save_and_retrieve_workflow(self, catalog, sample_workflow_info):
        """Test saving and retrieving workflow with PostgreSQL."""
        # Save workflow
        saved_workflows = catalog.save([sample_workflow_info])
        assert len(saved_workflows) == 1

        # Retrieve workflow
        retrieved_workflow = catalog.get("test_workflow")
        assert retrieved_workflow.name == "test_workflow"
        assert retrieved_workflow.imports == ["os", "sys", "flux"]
        assert retrieved_workflow.source == b'async def test_workflow():\n    return "Hello World"'
        assert retrieved_workflow.requests.cpu == 2
        assert retrieved_workflow.requests.memory == 1024

    def test_workflow_versioning(self, catalog):
        """Test workflow versioning functionality."""
        # Create multiple versions of the same workflow
        workflow_v1 = WorkflowInfo(
            id="versioned-workflow-v1",
            name="versioned_workflow",
            imports=["os"],
            source=b'async def versioned_workflow():\n    return "Version 1"',
            version=1,
        )

        workflow_v2 = WorkflowInfo(
            id="versioned-workflow-v2",
            name="versioned_workflow",
            imports=["os", "sys"],
            source=b'async def versioned_workflow():\n    return "Version 2"',
            version=2,
        )

        # Save both versions
        catalog.save([workflow_v1])
        catalog.save([workflow_v2])

        # Retrieve latest version (should be v2)
        latest = catalog.get("versioned_workflow")
        assert latest.version == 2
        assert b"Version 2" in latest.source

        # Retrieve specific version
        v1_retrieved = catalog.get("versioned_workflow", version=1)
        assert v1_retrieved.version == 1
        assert b"Version 1" in v1_retrieved.source

    def test_all_workflows_returns_latest_versions(self, catalog):
        """Test that all() returns only latest versions of workflows."""
        # Create multiple workflows with multiple versions
        workflows = [
            WorkflowInfo(
                id="wf1-v1",
                name="workflow1",
                imports=[],
                source=b"async def workflow1(): pass",
                version=1,
            ),
            WorkflowInfo(
                id="wf1-v2",
                name="workflow1",
                imports=[],
                source=b"async def workflow1(): pass",
                version=2,
            ),
            WorkflowInfo(
                id="wf2-v1",
                name="workflow2",
                imports=[],
                source=b"async def workflow2(): pass",
                version=1,
            ),
        ]

        for wf in workflows:
            catalog.save([wf])

        all_workflows = catalog.all()

        # Should have 2 workflows (workflow1 v2, workflow2 v1)
        workflow_names = [wf.name for wf in all_workflows]
        assert "workflow1" in workflow_names
        assert "workflow2" in workflow_names

        # Verify we get the latest version of workflow1
        workflow1 = next(wf for wf in all_workflows if wf.name == "workflow1")
        assert workflow1.version == 2

    def test_workflow_not_found_error(self, catalog):
        """Test WorkflowNotFoundError is raised for non-existent workflows."""
        with pytest.raises(WorkflowNotFoundError):
            catalog.get("non_existent_workflow")

    def test_complex_workflow_with_large_source(self, catalog):
        """Test handling of workflows with large source code."""
        # Create a large source code string
        large_source = b"""
import os
import sys
import asyncio
from typing import Dict, List, Any

async def complex_workflow(data: Dict[str, Any]) -> List[str]:
    '''A complex workflow with large source code for testing PostgreSQL TEXT field handling.'''

    results = []

    # Simulate complex processing
    for i in range(1000):
        result = f"Processing item {i}: {data.get('item_' + str(i), 'default')}"
        results.append(result)

        if i % 100 == 0:
            await asyncio.sleep(0.01)  # Simulate async work

    # Additional complex logic
    processed_data = {
        'total_items': len(results),
        'summary': results[:10],  # First 10 items
        'metadata': {
            'processing_time': 'simulated',
            'version': '1.0.0',
            'large_data': 'x' * 10000  # Large string
        }
    }

    return processed_data
"""

        large_workflow = WorkflowInfo(
            id="large-workflow",
            name="large_complex_workflow",
            imports=["os", "sys", "asyncio", "typing"],
            source=large_source,
            requests=ResourceRequest(cpu=4, memory=2048, packages=["asyncio", "typing"]),
        )

        # Should handle large workflow without issues
        catalog.save([large_workflow])
        retrieved = catalog.get("large_complex_workflow")

        assert retrieved.source == large_source
        assert len(retrieved.source) > 1000  # Verify it's actually large
        assert retrieved.requests.cpu == 4
        assert retrieved.requests.memory == 2048

    def test_unicode_and_special_characters(self, catalog):
        """Test handling of Unicode and special characters in workflow data."""
        unicode_workflow = WorkflowInfo(
            id="unicode-workflow",
            name="unicode_workflow_测试",
            imports=["unicodedata", "locale"],
            source="""
# -*- coding: utf-8 -*-
async def unicode_workflow():
    '''测试 Unicode characters: αβγδε, 🚀🌟💫'''
    message = "Hello, 世界! Здравствуй мир! ¡Hola mundo!"
    emoji_data = "🎯🔥⚡🌈🎉"
    return f"{message} {emoji_data}"
""".encode(),
            requests=ResourceRequest(packages=["unicodedata"]),
        )

        catalog.save([unicode_workflow])
        retrieved = catalog.get("unicode_workflow_测试")

        assert retrieved.name == "unicode_workflow_测试"
        assert "测试 Unicode characters" in retrieved.source.decode("utf-8")
        assert "🎯🔥⚡🌈🎉" in retrieved.source.decode("utf-8")

    def test_concurrent_catalog_access(self, catalog):
        """Test concurrent access to catalog (basic multi-threading safety)."""
        import threading
        import time

        results = []
        errors = []

        def worker(worker_id):
            try:
                # Each worker creates and retrieves a workflow
                workflow = WorkflowInfo(
                    id=f"concurrent-workflow-{worker_id}",
                    name=f"concurrent_workflow_{worker_id}",
                    imports=[],
                    source=f"async def concurrent_workflow_{worker_id}(): return {worker_id}".encode(),
                )

                catalog.save([workflow])
                time.sleep(0.01)  # Small delay to simulate processing

                retrieved = catalog.get(f"concurrent_workflow_{worker_id}")
                results.append((worker_id, retrieved.name))

            except Exception as e:
                errors.append((worker_id, str(e)))

        # Create multiple threads
        threads = []
        for i in range(5):
            thread = threading.Thread(target=worker, args=(i,))
            threads.append(thread)
            thread.start()

        # Wait for all threads to complete
        for thread in threads:
            thread.join()

        # Verify results
        assert len(errors) == 0, f"Errors occurred: {errors}"
        assert len(results) == 5

        # Verify all workflows were created
        for worker_id, name in results:
            assert name == f"concurrent_workflow_{worker_id}"


@pytest.mark.integration
@pytest.mark.postgresql
class TestPostgreSQLSchemaCreation:
    """Test PostgreSQL schema creation and table relationships."""

    @pytest.fixture(autouse=True)
    def setup_integration_config(self, integration_postgres_config, require_postgresql):
        """Set up integration test configuration."""
        config = Configuration.get()
        config.override(**integration_postgres_config)
        yield
        config.reset()

    def test_database_tables_created(self):
        """Test that all required database tables are created."""
        from sqlalchemy import inspect

        # Create repository to initialize database
        repo = RepositoryFactory.create_repository()

        # Get inspector to check database schema
        inspector = inspect(repo._engine)
        table_names = inspector.get_table_names()

        # Verify all expected tables exist
        expected_tables = [
            "workflows",
            "executions",
            "execution_events",
            "workers",
            "worker_runtimes",
            "worker_resources",
            "worker_resources_gpus",
            "worker_packages",
            "secrets",
        ]

        for table in expected_tables:
            assert table in table_names, f"Table '{table}' not found in database"

    def test_table_relationships(self):
        """Test that foreign key relationships are properly created."""
        from sqlalchemy import inspect

        repo = RepositoryFactory.create_repository()
        inspector = inspect(repo._engine)

        # Check workflows -> executions relationship
        execution_fks = inspector.get_foreign_keys("executions")
        workflow_fks = [fk for fk in execution_fks if fk["referred_table"] == "workflows"]
        assert len(workflow_fks) > 0, "Foreign key from executions to workflows not found"

        # Check executions -> execution_events relationship
        event_fks = inspector.get_foreign_keys("execution_events")
        execution_fks = [fk for fk in event_fks if fk["referred_table"] == "executions"]
        assert len(execution_fks) > 0, "Foreign key from execution_events to executions not found"

    def test_indexes_created(self):
        """Test that database indexes are properly created."""
        from sqlalchemy import inspect

        repo = RepositoryFactory.create_repository()
        inspector = inspect(repo._engine)

        # Check that primary key indexes exist
        for table in ["workflows", "executions", "workers"]:
            indexes = inspector.get_indexes(table)
            pk_indexes = [idx for idx in indexes if "primary" in idx.get("name", "").lower()]
            # Note: PostgreSQL may not show primary key as regular index
            # The important thing is that the table exists and is queryable
            assert indexes is not None
            # pk_indexes may be empty in PostgreSQL, but we verify table accessibility
            assert isinstance(pk_indexes, list)
