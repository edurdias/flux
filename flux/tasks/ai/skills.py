from __future__ import annotations

import logging
import re

from flux.errors import ExecutionError

logger = logging.getLogger("flux.skills")

_NAME_PATTERN = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")


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


def _validate_name(name: str) -> None:
    if not name:
        raise SkillValidationError("Skill name must not be empty.")
    if len(name) > 64:
        raise SkillValidationError(f"Skill name must not exceed 64 characters: '{name}'.")
    if "--" in name:
        raise SkillValidationError(f"Skill name must not contain consecutive hyphens: '{name}'.")
    if not _NAME_PATTERN.match(name):
        raise SkillValidationError(
            f"Skill name must be lowercase alphanumeric with single hyphens: '{name}'.",
        )


class Skill:
    def __init__(
        self,
        name: str,
        description: str,
        instructions: str,
        allowed_tools: list[str] | None = None,
        metadata: dict[str, str] | None = None,
    ):
        _validate_name(name)
        if not description:
            raise SkillValidationError("Skill description must not be empty.")
        if not instructions:
            raise SkillValidationError("Skill instructions must not be empty.")
        self.name = name
        self.description = description
        self.instructions = instructions
        self.allowed_tools = allowed_tools if allowed_tools is not None else []
        self.metadata = metadata if metadata is not None else {}

    def __repr__(self) -> str:
        return f"Skill(name='{self.name}')"
