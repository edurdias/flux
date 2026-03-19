from __future__ import annotations

from flux.errors import ExecutionError


class SkillValidationError(ValueError):
    pass


class SkillCatalogError(ValueError):
    pass


class SkillNotFoundError(ExecutionError):
    def __init__(self, name: str):
        super().__init__(message=f"Skill '{name}' not found.")
        self._skill_name = name

    @property
    def skill_name(self) -> str:
        return self._skill_name
