from __future__ import annotations

from sqlite3 import IntegrityError

import pytest
from pytest_mock import MockerFixture

import flux.decorators as decorators
from examples.hello_world import hello_world
from examples.parallel_tasks import parallel_tasks_workflow
from flux.catalogs import SQLiteWorkflowCatalog
from flux.catalogs import WorkflowCatalog
from flux.config import Configuration
from flux.errors import WorkflowNotFoundError


@pytest.fixture(autouse=True)
def setup():
    # Disable auto-registration for our tests
    Configuration().override(catalog={"auto_register": False})

    # Create a test catalog and save the hello_world workflow
    catalog = SQLiteWorkflowCatalog()

    # Clean up any existing workflows from previous test runs
    try:
        catalog.delete("hello_world")
        catalog.delete("parallel_tasks_workflow")
    except:  # noqa: E722
        pass

    yield

    # Clean up after tests
    try:
        catalog.delete("hello_world")
        catalog.delete("parallel_tasks_workflow")
    except:  # noqa: E722
        pass


def test_should_save_multiple_workflows():
    """Test that multiple workflows can be saved to the catalog."""
    catalog = SQLiteWorkflowCatalog()
    catalog.save(hello_world)
    catalog.save(parallel_tasks_workflow)

    workflows = catalog.all()
    workflow_names = [w.name for w in workflows]

    assert len(workflows) >= 2
    assert "hello_world" in workflow_names
    assert "parallel_tasks_workflow" in workflow_names


def test_should_save_multiple_versions():
    """Test that multiple versions of the same workflow can be saved."""
    catalog = SQLiteWorkflowCatalog()

    # Save first version
    catalog.save(hello_world)

    # Create a new version by wrapping the same workflow
    @decorators.workflow
    async def hello_world_v2(ctx):
        return await hello_world(ctx)

    hello_world_v2.name = "hello_world"  # Override the name to match

    # Save second version
    catalog.save(hello_world_v2)

    # Get latest version
    latest = catalog._get("hello_world")
    assert latest.version == 2, "The latest version should be 2"

    # Get specific version
    v1 = catalog._get("hello_world", 1)
    assert v1.version == 1, "Should be able to retrieve version 1"

    # Both versions should exist but be different objects
    assert v1.id != latest.id, "The two versions should have different IDs"


def test_should_get_latest_version_by_default():
    """Test that get() returns the latest version when version is not specified."""
    catalog = SQLiteWorkflowCatalog()

    # Setup multiple versions
    catalog.save(hello_world)

    # Create and save a second version
    @decorators.workflow
    async def hello_world_v2(ctx):
        return await hello_world(ctx)

    hello_world_v2.name = "hello_world"
    catalog.save(hello_world_v2)

    # Get without specifying version should return the latest
    workflow = catalog.get("hello_world")
    assert workflow.version == 2, "Should get the latest version (2)"


def test_should_get_specific_version():
    """Test that get() can retrieve a specific version."""
    catalog = SQLiteWorkflowCatalog()

    # Setup multiple versions
    catalog.save(hello_world)

    # Create and save a second version
    @decorators.workflow
    async def hello_world_v2(ctx):
        return await hello_world(ctx)

    hello_world_v2.name = "hello_world"
    catalog.save(hello_world_v2)

    # Get specific version
    workflow = catalog.get("hello_world", 1)
    assert workflow.version == 1, "Should get version 1"


def test_all_should_return_sorted_versions():
    """Test that all() returns workflows sorted by name and descending version."""
    catalog = SQLiteWorkflowCatalog()

    # Clean existing workflows
    try:
        catalog.delete("hello_world")
    except Exception:
        pass

    # Save first version of hello_world
    catalog.save(hello_world)

    # Create and save a second version of hello_world
    @decorators.workflow
    async def hello_world_v2(ctx):
        return await hello_world(ctx)

    hello_world_v2.name = "hello_world"
    catalog.save(hello_world_v2)

    # Get all hello_world workflows
    workflows = [w for w in catalog.all() if w.name == "hello_world"]

    # Check that hello_world workflows are sorted by version (descending)
    assert len(workflows) >= 2, "Should have at least 2 versions"
    assert workflows[0].name == "hello_world", "First workflow should be hello_world"
    assert workflows[0].version == 2, "First workflow should be version 2"
    assert workflows[1].name == "hello_world", "Second workflow should be hello_world"
    assert workflows[1].version == 1, "Second workflow should be version 1"


def test_all_should_return_workflows_with_expected_names():
    """Test that all() returns workflows with the expected names."""
    catalog = SQLiteWorkflowCatalog()

    # Clean up first
    try:
        catalog.delete("hello_world")
        catalog.delete("parallel_tasks_workflow")
    except Exception:
        pass

    # Save workflows in reverse alphabetical order
    catalog.save(parallel_tasks_workflow)
    catalog.save(hello_world)

    workflows = catalog.all()
    workflow_names = [w.name for w in workflows]

    # Check that our workflows are in the result
    assert "hello_world" in workflow_names, "hello_world should be in the workflows"
    assert (
        "parallel_tasks_workflow" in workflow_names
    ), "parallel_tasks_workflow should be in the workflows"


def test_delete_specific_version():
    """Test deleting a specific version of a workflow."""
    catalog = SQLiteWorkflowCatalog()

    # Setup multiple versions
    catalog.save(hello_world)

    # Create and save a second version
    @decorators.workflow
    async def hello_world_v2(ctx):
        return await hello_world(ctx)

    hello_world_v2.name = "hello_world"
    catalog.save(hello_world_v2)

    # Delete version 1
    catalog.delete("hello_world", 1)

    # Version 1 should be gone
    with pytest.raises(WorkflowNotFoundError):
        catalog.get("hello_world", 1)

    # Latest version (2) should still exist
    workflow = catalog.get("hello_world")
    assert workflow.version == 2, "Version 2 should still exist"


def test_delete_all_versions():
    """Test deleting all versions of a workflow."""
    catalog = SQLiteWorkflowCatalog()

    # Setup multiple versions
    catalog.save(hello_world)

    # Create and save a second version
    @decorators.workflow
    async def hello_world_v2(ctx):
        return await hello_world(ctx)

    hello_world_v2.name = "hello_world"
    catalog.save(hello_world_v2)

    # Delete all versions
    catalog.delete("hello_world")

    # No versions should exist
    with pytest.raises(WorkflowNotFoundError):
        catalog.get("hello_world")


def test_auto_register_from_module(monkeypatch):
    """Test auto-registration of workflows from a module."""
    # Configure to auto-register from examples module
    Configuration().override(catalog={"auto_register": True, "options": {"module": "examples"}})

    # Create catalog which should trigger auto-registration
    catalog = SQLiteWorkflowCatalog()

    # Check that workflows from examples module were registered
    workflows = catalog.all()
    workflow_names = [w.name for w in workflows]

    assert "hello_world" in workflow_names, "hello_world should be auto-registered"
    assert (
        "parallel_tasks_workflow" in workflow_names
    ), "parallel_tasks_workflow should be auto-registered"


def test_auto_register_from_path(monkeypatch):
    """Test auto-registration of workflows from a file path."""
    # Configure to auto-register from examples/hello_world.py
    Configuration().override(
        catalog={"auto_register": True, "options": {"path": "examples/hello_world.py"}},
    )

    # Create catalog which should trigger auto-registration
    catalog = SQLiteWorkflowCatalog()

    # Check that hello_world was registered
    workflow = catalog.get("hello_world")
    assert workflow.name == "hello_world", "hello_world should be auto-registered from path"


def test_integrity_error_handling(mocker: MockerFixture):
    """Test handling of integrity errors when saving workflows."""
    catalog = SQLiteWorkflowCatalog()

    # Mock the session to simulate an IntegrityError
    mock_session = mocker.patch.object(catalog, "session")
    mock_session.return_value.__enter__.return_value.add.side_effect = IntegrityError(
        "statement",
        "params",
        "orig",
    )
    mock_session.return_value.__enter__.return_value.commit.side_effect = IntegrityError(
        "statement",
        "params",
        "orig",
    )

    # Attempt to save should raise IntegrityError
    with pytest.raises(IntegrityError):
        catalog.save(hello_world)


def test_should_create_database():
    SQLiteWorkflowCatalog()


def test_should_save_workflow():
    catalog = SQLiteWorkflowCatalog()
    catalog.save(hello_world)
    return catalog


def test_should_get():
    catalog = test_should_save_workflow()
    workflow = catalog.get("hello_world")
    assert workflow, "The workflow should have been retrieved."
    assert isinstance(
        workflow.code,
        decorators.workflow,
    ), "The workflow should be an instance of the workflow decorator."
    return workflow.code


def test_should_execute():
    workflow = test_should_get()

    ctx = workflow.run("Joe")
    assert ctx.finished and ctx.succeeded, "The workflow should have been completed successfully."
    assert ctx.output == "Hello, Joe"


def test_should_raise_exception_when_not_found():
    workflow_name = "invalid_name"
    with pytest.raises(
        WorkflowNotFoundError,
        match=f"Workflow '{workflow_name}' not found",
    ):
        catalog = SQLiteWorkflowCatalog()
        catalog.get(workflow_name)


def test_workflow_catalog_factory():
    """Test that WorkflowCatalog.create() returns an SQLiteWorkflowCatalog instance."""
    catalog = WorkflowCatalog.create()
    assert isinstance(
        catalog,
        SQLiteWorkflowCatalog,
    ), "Should create SQLiteWorkflowCatalog instance"


def test_get_nonexistent_workflow():
    """Test error handling when getting a workflow that doesn't exist."""
    catalog = SQLiteWorkflowCatalog()
    with pytest.raises(WorkflowNotFoundError, match="Workflow 'nonexistent' not found"):
        catalog.get("nonexistent")


def test_get_nonexistent_version():
    """Test error handling when getting a version that doesn't exist."""
    # First create a workflow
    from examples.hello_world import hello_world

    catalog = SQLiteWorkflowCatalog()
    catalog.save(hello_world)

    # Try to get a version that doesn't exist
    with pytest.raises(WorkflowNotFoundError, match="Workflow 'hello_world' not found"):
        catalog.get("hello_world", 999)


def test_auto_register_with_no_options():
    """Test auto-register with no module or path options."""
    # Configure to auto-register but without specifying module or path
    Configuration().override(catalog={"auto_register": True, "options": {}})

    # Creating catalog should not raise errors even with invalid options
    SQLiteWorkflowCatalog()
