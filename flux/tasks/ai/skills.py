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

    @classmethod
    def from_file(cls, path: str) -> Skill:
        from pathlib import Path

        import yaml

        file_path = Path(path)
        if not file_path.exists():
            raise FileNotFoundError(f"Skill file not found: {path}")

        content = file_path.read_text(encoding="utf-8")

        if not content.startswith("---"):
            raise SkillValidationError(
                "Skill file must contain YAML frontmatter delimited by '---'.",
            )

        parts = content.split("---", 2)
        if len(parts) < 3:
            raise SkillValidationError(
                "Skill file must contain YAML frontmatter delimited by '---'.",
            )

        frontmatter = yaml.safe_load(parts[1])
        body = parts[2].strip()

        name = frontmatter.get("name", "")
        description = frontmatter.get("description", "")

        allowed_tools_raw = frontmatter.get("allowed-tools", "")
        allowed_tools = allowed_tools_raw.split() if allowed_tools_raw else []

        raw_metadata = frontmatter.get("metadata", {})
        metadata = {k: str(v) for k, v in raw_metadata.items()} if raw_metadata else {}

        dir_name = file_path.parent.name
        if name and name != dir_name:
            logger.warning(
                "Skill name '%s' does not match directory name '%s'.",
                name,
                dir_name,
            )

        return cls(
            name=name,
            description=description,
            instructions=body,
            allowed_tools=allowed_tools,
            metadata=metadata,
        )
