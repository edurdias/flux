from __future__ import annotations

import pytest

import flux.decorators as decorators
from examples.hello_world import hello_world
from flux.catalogs import SQLiteWorkflowCatalog
from flux.config import Configuration
from flux.errors import WorkflowNotFoundError


@pytest.fixture(autouse=True)
def setup():
    Configuration().override(catalog={"auto_register": False})


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
