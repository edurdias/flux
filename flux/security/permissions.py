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
    for index, nested in enumerate(nested_workflows):
        # Accept both ("ns", "name") tuples and ["ns", "name"] lists
        # (the catalog emits lists after JSON round-trip).
        if not isinstance(nested, (tuple, list)) or len(nested) != 2:
            raise ValueError(
                "nested_workflows entries must be 2-item tuples or lists of "
                f"(namespace, name); got invalid entry at index {index}: {nested!r}",
            )
        nested_ns, nested_name = nested
        perms.append(f"workflow:{nested_ns}:{nested_name}:run")
    return perms
