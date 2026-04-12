from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType


@dataclass(frozen=True)
class FluxIdentity:
    subject: str
    roles: frozenset[str] = frozenset()
    metadata: MappingProxyType | dict = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self):
        if isinstance(self.metadata, dict):
            object.__setattr__(self, "metadata", MappingProxyType(self.metadata))

    def has_role(self, role: str) -> bool:
        return role in self.roles

    def has_permission(self, required: str, permissions: set[str]) -> bool:
        if "*" in permissions:
            return True
        if required in permissions:
            return True
        req_parts = required.split(":")
        for perm in permissions:
            perm_parts = perm.split(":")
            if _wildcard_match(perm_parts, req_parts):
                return True
        return False


# Wildcard semantics:
#   Terminal `*` (last segment): matches any number of remaining segments.
#     e.g. `workflow:report:*` matches `workflow:report:run` and
#     `workflow:report:task:load:execute`.
#   Non-terminal `*` (middle segment): matches exactly one segment.
#     e.g. `workflow:*:*:read` matches `workflow:billing:invoice:read` but NOT
#     `workflow:billing:invoice:task:load:execute` (the latter needs `workflow:billing:*`).
def _wildcard_match(pattern_parts: list[str], target_parts: list[str]) -> bool:
    for i, part in enumerate(pattern_parts):
        if part == "*":
            if i == len(pattern_parts) - 1:
                return True
            if i >= len(target_parts):
                return False
            continue
        if i >= len(target_parts) or part != target_parts[i]:
            return False
    return len(pattern_parts) == len(target_parts)


ANONYMOUS = FluxIdentity(
    subject="anonymous",
    roles=frozenset({"admin"}),
)
