"""Tests for the SQLiteWorkflowCatalog implementation."""

from __future__ import annotations

import os
import tempfile
from unittest.mock import patch

import pytest

from flux.catalogs import DatabaseWorkflowCatalog
from flux.catalogs import WorkflowInfo
from flux.errors import WorkflowNotFoundError
from flux.models import Base


@pytest.fixture
def sqlite_workflow_catalog():
    """Create a SQLiteWorkflowCatalog with temporary DB for testing."""
    # Create a temporary file for the SQLite database
    with tempfile.NamedTemporaryFile(delete=False) as temp_file:
        db_path = temp_file.name

    # Configure temporary database URL
    with patch("flux.config.Configuration.get") as mock_config:
        mock_config.return_value.settings.database_url = f"sqlite:///{db_path}"
        mock_config.return_value.settings.database_type = "sqlite"

        # Create catalog instance
        catalog = DatabaseWorkflowCatalog()

        # Create tables
        Base.metadata.create_all(catalog._engine)

        yield catalog

    # Clean up the temporary file
    if os.path.exists(db_path):
        os.unlink(db_path)


@pytest.fixture
def sample_workflow():
    """Generate a sample workflow for testing."""
    return WorkflowInfo(
        id="test-workflow-id",
        name="test_workflow",
        imports=["import1", "import2"],
        source=b"""
import asyncio
from flux.decorators import workflow

@workflow
async def test_workflow():
    return "Hello World"
        """,
    )


def test_save_and_get_workflow(sqlite_workflow_catalog, sample_workflow):
    """Test saving a workflow and retrieving it."""
    # Save a workflow
    sqlite_workflow_catalog.save([sample_workflow])

    # Get the workflow back
    workflow = sqlite_workflow_catalog.get("default", "test_workflow")

    # Check that we got the right workflow
    assert workflow.name == "test_workflow"
    assert workflow.version == 1
    assert workflow.imports == ["import1", "import2"]
    assert workflow.source == sample_workflow.source


def test_all_workflows(sqlite_workflow_catalog, sample_workflow):
    """Test retrieving all workflows."""
    # Save a workflow
    sqlite_workflow_catalog.save([sample_workflow])

    # Create another workflow
    another_workflow = WorkflowInfo(
        id="another-workflow-id",
        name="another_workflow",
        imports=["import1"],
        source=b"""
import asyncio
from flux.decorators import workflow

@workflow
async def another_workflow():
    return "Another Hello World"
        """,
    )
    sqlite_workflow_catalog.save([another_workflow])

    # Get all workflows
    workflows = sqlite_workflow_catalog.all()

    # Should have two workflows
    assert len(workflows) == 2

    # Check names
    names = [w.name for w in workflows]
    assert "test_workflow" in names
    assert "another_workflow" in names


def test_workflow_not_found(sqlite_workflow_catalog):
    """Test that an exception is raised when a workflow is not found."""
    with pytest.raises(WorkflowNotFoundError) as excinfo:
        sqlite_workflow_catalog.get("default", "non_existent_workflow")

    assert "non_existent_workflow" in str(excinfo.value)


def test_delete_workflow(sqlite_workflow_catalog, sample_workflow):
    """Test deleting a workflow."""
    # Save a workflow
    sqlite_workflow_catalog.save([sample_workflow])

    # Delete the workflow
    sqlite_workflow_catalog.delete("default", "test_workflow")

    # Trying to get the workflow should raise WorkflowNotFoundError
    with pytest.raises(WorkflowNotFoundError):
        sqlite_workflow_catalog.get("default", "test_workflow")


def test_delete_specific_version(sqlite_workflow_catalog, sample_workflow):
    """Test deleting a specific version of a workflow."""
    # Save the workflow twice to create two versions
    sqlite_workflow_catalog.save([sample_workflow])
    sqlite_workflow_catalog.save([sample_workflow])

    # Delete only the first version
    sqlite_workflow_catalog.delete("default", "test_workflow", version=1)

    # Should still be able to get the second version
    workflow = sqlite_workflow_catalog.get("default", "test_workflow")
    assert workflow.version == 2

    # But trying to get the first version should fail
    with pytest.raises(WorkflowNotFoundError):
        sqlite_workflow_catalog.get("default", "test_workflow", version=1)


def test_parse_workflow(sqlite_workflow_catalog):
    """Test parsing a workflow from source code."""
    source = b"""
import asyncio
from flux.decorators import workflow

@workflow
async def test_parse_workflow():
    return "Hello Parsed World"
    """

    workflows = sqlite_workflow_catalog.parse(source)

    assert len(workflows) == 1
    assert workflows[0].name == "test_parse_workflow"
    assert workflows[0].imports == ["asyncio", "flux.decorators.workflow"]
    assert workflows[0].source == source


def test_parse_workflow_no_workflow_found(sqlite_workflow_catalog):
    """Test parsing source code with no workflow."""
    source = b"""
import asyncio

async def not_a_workflow():
    return "This is not a workflow"
    """

    with pytest.raises(SyntaxError) as excinfo:
        sqlite_workflow_catalog.parse(source)

    assert "No workflow found in the provided code" in str(excinfo.value)


def test_parse_workflow_syntax_error(sqlite_workflow_catalog):
    """Test parsing source code with syntax error."""
    source = b"""
import asyncio
from flux.decorators import workflow

@workflow
async def syntax_error_workflow():
    return "Missing closing quote
    """

    with pytest.raises(SyntaxError):
        sqlite_workflow_catalog.parse(source)


def test_workflow_info_to_dict(sample_workflow):
    """Test the to_dict method of WorkflowInfo."""
    result = sample_workflow.to_dict()

    assert result["name"] == "test_workflow"
    assert result["namespace"] == "default"
    assert result["version"] == 1
    assert result["imports"] == ["import1", "import2"]
    assert result["source"] == sample_workflow.source


def test_workflow_catalog_create():
    """Test the static create method returns a DatabaseWorkflowCatalog instance."""
    from flux.catalogs import WorkflowCatalog, DatabaseWorkflowCatalog

    catalog = WorkflowCatalog.create()
    assert isinstance(catalog, DatabaseWorkflowCatalog)


def test_versions_returns_all_versions(sqlite_workflow_catalog, sample_workflow):
    """Test that versions() returns all versions of a workflow."""
    # Save the workflow multiple times to create multiple versions
    sqlite_workflow_catalog.save([sample_workflow])
    sqlite_workflow_catalog.save([sample_workflow])
    sqlite_workflow_catalog.save([sample_workflow])

    # Get all versions
    versions = sqlite_workflow_catalog.versions("default", "test_workflow")

    # Should have 3 versions
    assert len(versions) == 3

    # Check version numbers (should be ordered descending - newest first)
    assert versions[0].version == 3
    assert versions[1].version == 2
    assert versions[2].version == 1

    # All should have the same name
    for v in versions:
        assert v.name == "test_workflow"


def test_versions_empty_for_nonexistent_workflow(sqlite_workflow_catalog):
    """Test that versions() returns empty list for non-existent workflow."""
    versions = sqlite_workflow_catalog.versions("default", "nonexistent_workflow")

    assert versions == []


def test_versions_ordered_descending(sqlite_workflow_catalog, sample_workflow):
    """Test that versions are ordered by version number descending."""
    # Save multiple versions
    sqlite_workflow_catalog.save([sample_workflow])
    sqlite_workflow_catalog.save([sample_workflow])

    versions = sqlite_workflow_catalog.versions("default", "test_workflow")

    # Verify descending order
    assert len(versions) == 2
    assert versions[0].version > versions[1].version


def test_versions_only_returns_requested_workflow(sqlite_workflow_catalog, sample_workflow):
    """Test that versions() only returns versions of the requested workflow."""
    # Save the sample workflow
    sqlite_workflow_catalog.save([sample_workflow])
    sqlite_workflow_catalog.save([sample_workflow])

    # Create and save another workflow
    another_workflow = WorkflowInfo(
        id="another-workflow-id",
        name="another_workflow",
        imports=["import1"],
        source=b"async def another_workflow(): pass",
    )
    sqlite_workflow_catalog.save([another_workflow])

    # Get versions of test_workflow only
    versions = sqlite_workflow_catalog.versions("default", "test_workflow")

    # Should only have 2 versions (not 3)
    assert len(versions) == 2
    for v in versions:
        assert v.name == "test_workflow"


def test_workflow_model_has_namespace_column(tmp_path):
    from flux.models import Base
    from sqlalchemy import create_engine, inspect

    db_url = f"sqlite:///{tmp_path}/test_models.db"
    engine = create_engine(db_url)
    Base.metadata.create_all(engine)

    inspector = inspect(engine)
    cols = {c["name"] for c in inspector.get_columns("workflows")}
    assert "namespace" in cols

    exec_cols = {c["name"] for c in inspector.get_columns("executions")}
    assert "workflow_namespace" in exec_cols

    sched_cols = {c["name"] for c in inspector.get_columns("schedules")}
    assert "workflow_namespace" in sched_cols


def test_workflow_model_unique_constraint_on_namespace_name_version(tmp_path):
    from flux.models import Base
    from sqlalchemy import create_engine, inspect

    db_url = f"sqlite:///{tmp_path}/test_models.db"
    engine = create_engine(db_url)
    Base.metadata.create_all(engine)

    inspector = inspect(engine)
    unique_constraints = inspector.get_unique_constraints("workflows")
    names = {uc["name"] for uc in unique_constraints}
    assert "uix_workflow_namespace_name_version" in names


def test_workflow_model_composite_namespace_name_index(tmp_path):
    from flux.models import Base
    from sqlalchemy import create_engine, inspect

    db_url = f"sqlite:///{tmp_path}/test_models.db"
    engine = create_engine(db_url)
    Base.metadata.create_all(engine)

    inspector = inspect(engine)
    indexes = inspector.get_indexes("workflows")
    names = {idx["name"] for idx in indexes}
    assert "ix_workflow_namespace_name" in names

    target = next(idx for idx in indexes if idx["name"] == "ix_workflow_namespace_name")
    assert target["column_names"] == ["namespace", "name"]
    assert "uix_workflow_name_version" not in names


def test_workflow_info_carries_namespace():
    from flux.catalogs import WorkflowInfo

    info = WorkflowInfo(
        id="billing/invoice",
        name="invoice",
        namespace="billing",
        imports=[],
        source=b"",
    )
    assert info.namespace == "billing"
    assert info.name == "invoice"
    assert info.qualified_name == "billing/invoice"


def test_catalog_get_scopes_by_namespace(tmp_path):
    db_path = tmp_path / "catalog.db"
    with patch("flux.config.Configuration.get") as mock_config:
        mock_config.return_value.settings.database_url = f"sqlite:///{db_path}"
        mock_config.return_value.settings.database_type = "sqlite"

        from flux.catalogs import DatabaseWorkflowCatalog, WorkflowInfo

        catalog = DatabaseWorkflowCatalog()
        Base.metadata.create_all(catalog._engine)
        catalog.save(
            [
                WorkflowInfo(id="", name="process", namespace="billing", imports=[], source=b"x"),
                WorkflowInfo(id="", name="process", namespace="analytics", imports=[], source=b"y"),
            ],
        )

        billing = catalog.get("billing", "process")
        analytics = catalog.get("analytics", "process")
        assert billing.namespace == "billing"
        assert analytics.namespace == "analytics"
        assert billing.source != analytics.source


def test_catalog_list_namespaces(tmp_path):
    db_path = tmp_path / "catalog2.db"
    with patch("flux.config.Configuration.get") as mock_config:
        mock_config.return_value.settings.database_url = f"sqlite:///{db_path}"
        mock_config.return_value.settings.database_type = "sqlite"

        from flux.catalogs import DatabaseWorkflowCatalog, WorkflowInfo

        catalog = DatabaseWorkflowCatalog()
        Base.metadata.create_all(catalog._engine)
        catalog.save(
            [
                WorkflowInfo(id="", name="a", namespace="default", imports=[], source=b"x"),
                WorkflowInfo(id="", name="b", namespace="billing", imports=[], source=b"y"),
            ],
        )

        assert sorted(catalog.list_namespaces()) == ["billing", "default"]


def test_catalog_save_and_get_preserves_resource_requests(tmp_path):
    db_path = tmp_path / "requests.db"
    with patch("flux.config.Configuration.get") as mock_config:
        mock_config.return_value.settings.database_url = f"sqlite:///{db_path}"
        mock_config.return_value.settings.database_type = "sqlite"

        from flux.catalogs import DatabaseWorkflowCatalog, WorkflowInfo
        from flux.domain.resource_request import ResourceRequest

        catalog = DatabaseWorkflowCatalog()
        Base.metadata.create_all(catalog._engine)
        catalog.save(
            [
                WorkflowInfo(
                    id="",
                    name="heavy",
                    namespace="default",
                    imports=[],
                    source=b"",
                    requests=ResourceRequest(cpu=4, memory="2Gi", packages=["numpy"]),
                ),
            ],
        )

        fetched = catalog.get("default", "heavy")
        assert fetched.requests is not None
        assert fetched.requests.cpu == 4
        assert fetched.requests.memory == "2Gi"
        assert fetched.requests.packages == ["numpy"]


def test_parse_extracts_namespace_from_with_options():
    from flux.catalogs import DatabaseWorkflowCatalog

    source = b"""
from flux import workflow

@workflow.with_options(name="invoice", namespace="billing")
async def invoice(ctx):
    return None
"""
    catalog = DatabaseWorkflowCatalog.__new__(DatabaseWorkflowCatalog)
    infos = catalog.parse(source)
    assert len(infos) == 1
    assert infos[0].namespace == "billing"
    assert infos[0].name == "invoice"
    assert infos[0].id == "billing/invoice"


def test_parse_defaults_namespace_when_not_declared():
    from flux.catalogs import DatabaseWorkflowCatalog

    source = b"""
from flux import workflow

@workflow
async def hello(ctx):
    return None
"""
    catalog = DatabaseWorkflowCatalog.__new__(DatabaseWorkflowCatalog)
    infos = catalog.parse(source)
    assert infos[0].namespace == "default"
    assert infos[0].id == "default/hello"


def test_extract_metadata_records_nested_workflow_tuples():
    from flux.catalogs import DatabaseWorkflowCatalog

    source = b"""
from flux import workflow, task

@task
async def load():
    return None

@workflow.with_options(namespace="billing")
async def nested_helper(ctx):
    return None

@workflow.with_options(namespace="billing")
async def main(ctx):
    await load()
    await nested_helper(ctx)
"""
    catalog = DatabaseWorkflowCatalog.__new__(DatabaseWorkflowCatalog)
    infos = catalog.parse(source)
    main_info = next(i for i in infos if i.name == "main")
    assert main_info.metadata["task_names"] == ["load"]
    assert main_info.metadata["nested_workflows"] == [["billing", "nested_helper"]]


def test_nested_plain_workflow_maps_to_default_namespace_from_namespaced_caller():
    from flux.catalogs import DatabaseWorkflowCatalog

    source = b"""
from flux import workflow

@workflow
async def nested_helper(ctx):
    return None

@workflow.with_options(namespace="billing")
async def main(ctx):
    await nested_helper(ctx)
"""
    catalog = DatabaseWorkflowCatalog.__new__(DatabaseWorkflowCatalog)
    infos = catalog.parse(source)
    main_info = next(i for i in infos if i.name == "main")
    assert main_info.metadata["nested_workflows"] == [["default", "nested_helper"]]
