from __future__ import annotations


def generate_permission_tree(
    namespace: str,
    workflow_name: str,
    task_names: list[str],
    nested_workflows: list[tuple[str, str]] | list[list[str]],
) -> list[str]:
    perms = [
        f"workflow:{namespace}:{workflow_name}:run",
        f"workflow:{namespace}:{workflow_name}:read",
    ]
    for task_name in task_names:
        perms.append(f"workflow:{namespace}:{workflow_name}:task:{task_name}:execute")
    for nested in nested_workflows:
        # Accept both ("ns", "name") tuples and ["ns", "name"] lists
        # (the catalog emits lists after JSON round-trip).
        nested_ns, nested_name = nested[0], nested[1]
        perms.append(f"workflow:{nested_ns}:{nested_name}:run")
    return perms
