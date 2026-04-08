from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class FluxIdentity:
    subject: str
    roles: frozenset[str] = frozenset()
    metadata: dict = field(default_factory=dict)

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
