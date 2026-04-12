from flux.security.permissions import generate_permission_tree


class TestPermissionTreeGeneration:
    def test_simple_workflow(self):
        tree = generate_permission_tree(
            namespace="default",
            workflow_name="customer_report",
            task_names=["load_data", "generate_report"],
            nested_workflows=[],
        )
        assert "workflow:default:customer_report:run" in tree
        assert "workflow:default:customer_report:read" in tree
        assert "workflow:default:customer_report:task:load_data:execute" in tree
        assert "workflow:default:customer_report:task:generate_report:execute" in tree

    def test_workflow_with_nested(self):
        tree = generate_permission_tree(
            namespace="default",
            workflow_name="pipeline",
            task_names=["step_one"],
            nested_workflows=[("default", "data_sync")],
        )
        assert "workflow:default:pipeline:run" in tree
        assert "workflow:default:pipeline:task:step_one:execute" in tree
        assert "workflow:default:data_sync:run" in tree

    def test_empty_tasks(self):
        tree = generate_permission_tree(
            namespace="default",
            workflow_name="empty",
            task_names=[],
            nested_workflows=[],
        )
        assert tree == ["workflow:default:empty:run", "workflow:default:empty:read"]


def test_generate_permission_tree_4_segment():
    from flux.security.permissions import generate_permission_tree

    perms = generate_permission_tree(
        namespace="billing",
        workflow_name="invoice",
        task_names=["load", "save"],
        nested_workflows=[("analytics", "summarize")],
    )
    assert "workflow:billing:invoice:run" in perms
    assert "workflow:billing:invoice:read" in perms
    assert "workflow:billing:invoice:task:load:execute" in perms
    assert "workflow:billing:invoice:task:save:execute" in perms
    assert "workflow:analytics:summarize:run" in perms


def test_generate_permission_tree_accepts_nested_as_list():
    """Catalog's _extract_workflow_metadata produces list-of-lists."""
    from flux.security.permissions import generate_permission_tree

    perms = generate_permission_tree(
        namespace="default",
        workflow_name="outer",
        task_names=[],
        nested_workflows=[["billing", "inner"]],
    )
    assert "workflow:billing:inner:run" in perms


def test_generate_permission_tree_rejects_malformed_nested_entries():
    """Malformed nested_workflows entries must raise ValueError, not IndexError."""
    import pytest
    from flux.security.permissions import generate_permission_tree

    # Wrong-length list
    with pytest.raises(ValueError, match="nested_workflows entries must be"):
        generate_permission_tree(
            namespace="default",
            workflow_name="outer",
            task_names=[],
            nested_workflows=[["only_one"]],  # type: ignore[list-item]
        )

    # Plain string instead of a pair
    with pytest.raises(ValueError, match="nested_workflows entries must be"):
        generate_permission_tree(
            namespace="default",
            workflow_name="outer",
            task_names=[],
            nested_workflows=["not_a_pair"],  # type: ignore[list-item]
        )

    # Triple
    with pytest.raises(ValueError, match="nested_workflows entries must be"):
        generate_permission_tree(
            namespace="default",
            workflow_name="outer",
            task_names=[],
            nested_workflows=[["a", "b", "c"]],  # type: ignore[list-item]
        )
