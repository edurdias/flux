"""Tests for parsing workflow resources in the WorkflowCatalog."""

from __future__ import annotations

import pytest

from flux.catalogs import WorkflowCatalog
from flux.catalogs import WorkflowInfo


# Create a minimal implementation of WorkflowCatalog for testing
class TestWorkflowCatalog(WorkflowCatalog):
    """A minimal implementation of WorkflowCatalog for testing the parse method."""

    def all(self) -> list[WorkflowInfo]:
        """Return an empty list for testing purposes."""
        return []

    def get(self, name: str, version: int | None = None) -> WorkflowInfo:
        """Raise an exception as this method is not needed for testing parse."""
        raise NotImplementedError("This method is not used in the tests")

    def save(self, workflows: list[WorkflowInfo]):
        """Do nothing as this method is not needed for testing parse."""
        pass

    def delete(self, name: str, version: int | None = None):
        """Do nothing as this method is not needed for testing parse."""
        pass


@pytest.fixture
def workflow_catalog():
    """Create a test WorkflowCatalog instance for testing the parse method."""
    return TestWorkflowCatalog()


def test_parse_workflow_with_direct_requests(workflow_catalog):
    """Test parsing a workflow with direct ResourceRequest constructor."""
    source = b"""
import asyncio
from flux import ExecutionContext
from flux.workflow import workflow
from flux.domain import ResourceRequest

@workflow.with_options(
    name="cpu_intensive_workflow",
    requests=ResourceRequest(cpu=4, memory="8Gi")
)
async def cpu_intensive_workflow(ctx: ExecutionContext):
    return "Workflow with CPU and memory requirements"
    """

    workflows = workflow_catalog.parse(source)

    assert len(workflows) == 1
    assert workflows[0].name == "cpu_intensive_workflow"
    assert workflows[0].requests is not None
    assert workflows[0].requests.cpu == 4
    assert workflows[0].requests.memory == "8Gi"
    assert workflows[0].requests.gpu is None
    assert workflows[0].requests.disk is None
    assert workflows[0].requests.packages is None


def test_parse_workflow_with_factory_methods(workflow_catalog):
    """Test parsing a workflow with ResourceRequest factory methods."""
    source = b"""
import asyncio
from flux import ExecutionContext
from flux.workflow import workflow
from flux.domain import ResourceRequest

@workflow.with_options(
    name="gpu_workflow",
    requests=ResourceRequest.with_gpu(2)
)
async def gpu_workflow(ctx: ExecutionContext):
    return "Workflow with GPU requirements"
    """

    workflows = workflow_catalog.parse(source)

    assert len(workflows) == 1
    assert workflows[0].name == "gpu_workflow"
    assert workflows[0].requests is not None
    assert workflows[0].requests.gpu == 2
    assert workflows[0].requests.cpu is None
    assert workflows[0].requests.memory is None
    assert workflows[0].requests.disk is None
    assert workflows[0].requests.packages is None


def test_parse_workflow_with_package_requirements(workflow_catalog):
    """Test parsing a workflow with package requirements."""
    source = b"""
import asyncio
from flux import ExecutionContext
from flux.workflow import workflow
from flux.domain import ResourceRequest

@workflow.with_options(
    name="ml_workflow",
    requests=ResourceRequest.with_packages(["tensorflow>=2.0.0", "numpy>=1.20.0", "pandas"])
)
async def ml_workflow(ctx: ExecutionContext):
    return "Workflow with package requirements"
    """

    workflows = workflow_catalog.parse(source)

    assert len(workflows) == 1
    assert workflows[0].name == "ml_workflow"
    assert workflows[0].requests is not None
    assert workflows[0].requests.packages == ["tensorflow>=2.0.0", "numpy>=1.20.0", "pandas"]
    assert workflows[0].requests.cpu is None
    assert workflows[0].requests.memory is None
    assert workflows[0].requests.gpu is None
    assert workflows[0].requests.disk is None


def test_parse_workflow_with_combined_requirements(workflow_catalog):
    """Test parsing a workflow with combined requirements."""
    source = b"""
import asyncio
from flux import ExecutionContext
from flux.workflow import workflow
from flux.domain import ResourceRequest

@workflow.with_options(
    name="combined_workflow",
    requests=ResourceRequest(
        cpu=8,
        memory="16Gi",
        gpu=1,
        disk=100,
        packages=["scikit-learn", "matplotlib>=3.5.0"]
    )
)
async def combined_workflow(ctx: ExecutionContext):
    return "Workflow with combined requirements"
    """

    workflows = workflow_catalog.parse(source)

    assert len(workflows) == 1
    assert workflows[0].name == "combined_workflow"
    assert workflows[0].requests is not None
    assert workflows[0].requests.cpu == 8
    assert workflows[0].requests.memory == "16Gi"
    assert workflows[0].requests.gpu == 1
    assert workflows[0].requests.disk == 100
    assert workflows[0].requests.packages == ["scikit-learn", "matplotlib>=3.5.0"]


def test_parse_multiple_workflows_with_different_requirements(workflow_catalog):
    """Test parsing multiple workflows with different requirements in the same file."""
    source = b"""
import asyncio
from flux import ExecutionContext
from flux.workflow import workflow
from flux.domain import ResourceRequest

@workflow.with_options(
    name="data_workflow",
    requests=ResourceRequest(memory="4Gi", packages=["pandas", "numpy"])
)
async def data_workflow(ctx: ExecutionContext):
    return "Data processing workflow"

@workflow.with_options(
    name="training_workflow",
    requests=ResourceRequest.with_gpu(1)
)
async def training_workflow(ctx: ExecutionContext):
    return "Model training workflow"

@workflow
async def simple_workflow(ctx: ExecutionContext):
    return "Simple workflow without requirements"
    """

    workflows = workflow_catalog.parse(source)

    assert len(workflows) == 3

    # Find workflows by name
    data_wf = next(wf for wf in workflows if wf.name == "data_workflow")
    training_wf = next(wf for wf in workflows if wf.name == "training_workflow")
    simple_wf = next(wf for wf in workflows if wf.name == "simple_workflow")

    # Check data workflow
    assert data_wf.requests is not None
    assert data_wf.requests.memory == "4Gi"
    assert data_wf.requests.packages == ["pandas", "numpy"]

    # Check training workflow
    assert training_wf.requests is not None
    assert training_wf.requests.gpu == 1

    # Check simple workflow
    assert simple_wf.requests is None


def test_parse_workflow_without_requests(workflow_catalog):
    """Test parsing a workflow without any resource requests."""
    source = b"""
import asyncio
from flux import ExecutionContext
from flux.workflow import workflow

@workflow
async def simple_workflow(ctx: ExecutionContext):
    return "Simple workflow without requirements"
    """

    workflows = workflow_catalog.parse(source)

    assert len(workflows) == 1
    assert workflows[0].name == "simple_workflow"
    assert workflows[0].requests is None
