from __future__ import annotations

import pytest

from examples.hello_world import hello_world
from flux.catalogs import ModuleWorkflowCatalog
from flux.errors import WorkflowNotFoundError


def test_should_get_from_module():
    workflow_name = "hello_world"
    module_name = f"examples.{workflow_name}"
    options = {"module": module_name}
    catalog = ModuleWorkflowCatalog(options)
    workflow = catalog.get(workflow_name)
    assert workflow
    assert workflow == hello_world


def test_should_get_from_path():
    workflow_name = "hello_world"
    options = {"path": "examples/hello_world.py"}
    catalog = ModuleWorkflowCatalog(options)
    workflow = catalog.get(workflow_name)
    assert workflow


def test_should_raise_exception_path_is_invalid():
    options = {"path": "invalid_path"}
    with pytest.raises(ImportError, match=f"Cannot find module at {options["path"]}."):
        ModuleWorkflowCatalog(options)


def test_should_raise_exception_when_not_found():
    workflow_name = "hello_world"
    with pytest.raises(
        WorkflowNotFoundError,
        match=f"Workflow '{workflow_name}' not found",
    ):
        catalog = ModuleWorkflowCatalog()
        catalog.get(workflow_name)
