from flux.security.permissions import generate_permission_tree


class TestPermissionTreeGeneration:
    def test_simple_workflow(self):
        tree = generate_permission_tree(
            workflow_name="customer_report",
            task_names=["load_data", "generate_report"],
            nested_workflows=[],
        )
        assert "workflow:customer_report:run" in tree
        assert "workflow:customer_report:read" in tree
        assert "workflow:customer_report:task:load_data:execute" in tree
        assert "workflow:customer_report:task:generate_report:execute" in tree

    def test_workflow_with_nested(self):
        tree = generate_permission_tree(
            workflow_name="pipeline",
            task_names=["step_one"],
            nested_workflows=["data_sync"],
        )
        assert "workflow:pipeline:run" in tree
        assert "workflow:pipeline:task:step_one:execute" in tree
        assert "workflow:data_sync:run" in tree

    def test_empty_tasks(self):
        tree = generate_permission_tree(
            workflow_name="empty",
            task_names=[],
            nested_workflows=[],
        )
        assert tree == ["workflow:empty:run", "workflow:empty:read"]
