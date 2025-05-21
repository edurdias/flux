"""Tests for the SQLiteWorkflowCatalog implementation."""
from __future__ import annotations

import os
import tempfile
from unittest.mock import patch

import pytest

from flux.catalogs import SQLiteWorkflowCatalog
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

        # Create catalog instance
        catalog = SQLiteWorkflowCatalog()

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
    workflow = sqlite_workflow_catalog.get("test_workflow")

    # Check that we got the right workflow
    assert workflow.name == "test_workflow"
    assert workflow.version == 1
    assert workflow.imports == ["import1", "import2"]
    assert workflow.source == sample_workflow.source


def test_get_workflow_with_version(sqlite_workflow_catalog, sample_workflow):
    """Test retrieving a specific version of a workflow."""
    # Save the workflow twice to create two versions
    sqlite_workflow_catalog.save([sample_workflow])

    # Create a slightly modified version
    updated_workflow = WorkflowInfo(
        name="test_workflow",
        imports=["import1", "import2", "import3"],
        source=b"""
import asyncio
from flux.decorators import workflow

@workflow
async def test_workflow():
    return "Hello Updated World"
        """,
    )
    sqlite_workflow_catalog.save([updated_workflow])

    # Get the first version
    workflow_v1 = sqlite_workflow_catalog.get("test_workflow", version=1)
    assert workflow_v1.version == 1

    # Get the second version
    workflow_v2 = sqlite_workflow_catalog.get("test_workflow", version=2)
    assert workflow_v2.version == 2

    # Without specifying a version, should get the latest
    latest_workflow = sqlite_workflow_catalog.get("test_workflow")
    assert latest_workflow.version == 2


def test_all_workflows(sqlite_workflow_catalog, sample_workflow):
    """Test retrieving all workflows."""
    # Save a workflow
    sqlite_workflow_catalog.save([sample_workflow])

    # Create another workflow
    another_workflow = WorkflowInfo(
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
        sqlite_workflow_catalog.get("non_existent_workflow")

    assert "Workflow 'non_existent_workflow' not found" in str(excinfo.value)


def test_delete_workflow(sqlite_workflow_catalog, sample_workflow):
    """Test deleting a workflow."""
    # Save a workflow
    sqlite_workflow_catalog.save([sample_workflow])

    # Delete the workflow
    sqlite_workflow_catalog.delete("test_workflow")

    # Trying to get the workflow should raise WorkflowNotFoundError
    with pytest.raises(WorkflowNotFoundError):
        sqlite_workflow_catalog.get("test_workflow")


def test_delete_specific_version(sqlite_workflow_catalog, sample_workflow):
    """Test deleting a specific version of a workflow."""
    # Save the workflow twice to create two versions
    sqlite_workflow_catalog.save([sample_workflow])
    sqlite_workflow_catalog.save([sample_workflow])

    # Delete only the first version
    sqlite_workflow_catalog.delete("test_workflow", version=1)

    # Should still be able to get the second version
    workflow = sqlite_workflow_catalog.get("test_workflow")
    assert workflow.version == 2

    # But trying to get the first version should fail
    with pytest.raises(WorkflowNotFoundError):
        sqlite_workflow_catalog.get("test_workflow", version=1)


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
    assert result["version"] == 1
    assert result["imports"] == ["import1", "import2"]
    assert result["source"] == sample_workflow.source


def test_workflow_catalog_create():
    """Test the static create method returns an SQLiteWorkflowCatalog instance."""
    from flux.catalogs import WorkflowCatalog

    catalog = WorkflowCatalog.create()
    assert isinstance(catalog, SQLiteWorkflowCatalog)
