from flux.catalogs import WorkflowCatalog


class FakeCatalog(WorkflowCatalog):
    def all(self, namespace=None):
        return []

    def get(self, namespace, name, version=None):
        raise NotImplementedError()

    def save(self, workflows):
        pass

    def delete(self, namespace, name, version=None):
        pass

    def versions(self, namespace, name):
        return []

    def list_namespaces(self):
        return []


class TestWorkflowMetadataExtraction:
    def test_extract_task_names(self):
        source = b"""
from flux import workflow, task

@task
async def load_data(source: str) -> dict:
    return {"records": 100}

@task
async def generate_report(data: dict) -> str:
    return "done"

@workflow
async def my_workflow(ctx):
    data = await load_data("db")
    return await generate_report(data)
"""
        catalog = FakeCatalog()
        workflows = catalog.parse(source)
        assert len(workflows) == 1
        wf = workflows[0]
        assert wf.metadata is not None
        assert "load_data" in wf.metadata.get("task_names", [])
        assert "generate_report" in wf.metadata.get("task_names", [])

    def test_extract_nested_workflow_calls(self):
        source = b"""
from flux import workflow, task

@task
async def step_one():
    return 1

@workflow
async def inner_workflow(ctx):
    return await step_one()

@workflow
async def outer_workflow(ctx):
    await step_one()
    await inner_workflow(ctx)
"""
        catalog = FakeCatalog()
        workflows = catalog.parse(source)
        outer = [w for w in workflows if w.name == "outer_workflow"][0]
        assert "step_one" in outer.metadata.get("task_names", [])
        assert ["default", "inner_workflow"] in outer.metadata.get("nested_workflows", [])

    def test_extract_task_with_options_name(self):
        source = b"""
from flux import workflow, task

@task.with_options(name="custom_name")
async def my_func():
    return 1

@workflow
async def my_workflow(ctx):
    return await my_func()
"""
        catalog = FakeCatalog()
        workflows = catalog.parse(source)
        wf = workflows[0]
        assert "custom_name" in wf.metadata.get("task_names", [])

    def test_task_with_options_uses_custom_name_in_metadata(self):
        source = b"""
from flux import workflow, task

@task.with_options(name="custom_{source}")
async def load_data(source: str):
    return source

@workflow
async def my_workflow(ctx):
    return await load_data("db")
"""
        catalog = FakeCatalog()
        workflows = catalog.parse(source)
        wf = workflows[0]
        assert "custom_{source}" in wf.metadata.get("task_names", [])
        assert "load_data" not in wf.metadata.get("task_names", [])

    def test_task_without_custom_name_uses_function_name(self):
        source = b"""
from flux import workflow, task

@task
async def simple_task():
    return 1

@task.with_options(retry_max_attempts=3)
async def retryable_task():
    return 2

@workflow
async def my_workflow(ctx):
    await simple_task()
    return await retryable_task()
"""
        catalog = FakeCatalog()
        workflows = catalog.parse(source)
        wf = workflows[0]
        assert "simple_task" in wf.metadata.get("task_names", [])
        assert "retryable_task" in wf.metadata.get("task_names", [])

    def test_auth_exempt_task_excluded_from_metadata(self):
        source = b"""
from flux import workflow, task

@task
async def normal_task():
    return 1

@task.with_options(auth_exempt=True)
async def exempt_task():
    return 2

@workflow
async def my_workflow(ctx):
    await normal_task()
    return await exempt_task()
"""
        catalog = FakeCatalog()
        workflows = catalog.parse(source)
        wf = workflows[0]
        assert "normal_task" in wf.metadata.get("task_names", [])
        assert "exempt_task" not in wf.metadata.get("task_names", [])

    def test_auth_exempt_task_included_in_auth_exempt_tasks(self):
        source = b"""
from flux import workflow, task

@task
async def normal_task():
    return 1

@task.with_options(auth_exempt=True)
async def exempt_task():
    return 2

@workflow
async def my_workflow(ctx):
    await normal_task()
    return await exempt_task()
"""
        catalog = FakeCatalog()
        workflows = catalog.parse(source)
        wf = workflows[0]
        assert "exempt_task" in wf.metadata.get("auth_exempt_tasks", [])
        assert "normal_task" not in wf.metadata.get("auth_exempt_tasks", [])

    def test_metadata_has_auth_exempt_tasks_key(self):
        source = b"""
from flux import workflow, task

@task
async def my_task():
    return 1

@workflow
async def my_workflow(ctx):
    return await my_task()
"""
        catalog = FakeCatalog()
        workflows = catalog.parse(source)
        wf = workflows[0]
        assert "auth_exempt_tasks" in wf.metadata
        assert wf.metadata["auth_exempt_tasks"] == []

    def test_auth_exempt_false_task_included_in_metadata(self):
        source = b"""
from flux import workflow, task

@task.with_options(auth_exempt=False)
async def non_exempt_task():
    return 1

@workflow
async def my_workflow(ctx):
    return await non_exempt_task()
"""
        catalog = FakeCatalog()
        workflows = catalog.parse(source)
        wf = workflows[0]
        assert "non_exempt_task" in wf.metadata.get("task_names", [])

    def test_nested_workflow_with_options_name_used(self):
        source = b"""
from flux import workflow, task

@task
async def step_one():
    return 1

@workflow.with_options(name="renamed_inner")
async def inner_workflow(ctx):
    return await step_one()

@workflow
async def outer_workflow(ctx):
    await inner_workflow(ctx)
"""
        catalog = FakeCatalog()
        workflows = catalog.parse(source)
        outer = [w for w in workflows if w.name == "outer_workflow"][0]
        assert ["default", "renamed_inner"] in outer.metadata.get("nested_workflows", [])
        assert ["default", "inner_workflow"] not in outer.metadata.get("nested_workflows", [])

    def test_nested_workflow_plain_decorator_uses_function_name(self):
        source = b"""
from flux import workflow, task

@task
async def step():
    return 1

@workflow
async def sub_workflow(ctx):
    return await step()

@workflow
async def main_workflow(ctx):
    await sub_workflow(ctx)
"""
        catalog = FakeCatalog()
        workflows = catalog.parse(source)
        main = [w for w in workflows if w.name == "main_workflow"][0]
        assert ["default", "sub_workflow"] in main.metadata.get("nested_workflows", [])
