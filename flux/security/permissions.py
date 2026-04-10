from __future__ import annotations


def generate_permission_tree(
    workflow_name: str,
    task_names: list[str],
    nested_workflows: list[str],
) -> list[str]:
    perms = [
        f"workflow:{workflow_name}:run",
        f"workflow:{workflow_name}:read",
    ]
    for task_name in task_names:
        perms.append(f"workflow:{workflow_name}:task:{task_name}:execute")
    for nested_name in nested_workflows:
        perms.append(f"workflow:{nested_name}:run")
    return perms
