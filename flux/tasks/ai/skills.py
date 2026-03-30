from __future__ import annotations

import logging
import re

from flux.errors import ExecutionError
from flux.task import task

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
        if not isinstance(frontmatter, dict):
            raise SkillValidationError(
                "Skill frontmatter must be a YAML mapping.",
            )
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


class SkillCatalog:
    def __init__(self, skills: list[Skill] | None = None):
        self._skills: dict[str, Skill] = {}
        for skill in skills or []:
            self.register(skill)

    def register(self, skill: Skill) -> None:
        if skill.name in self._skills:
            raise SkillCatalogError(f"Skill '{skill.name}' is already registered.")
        self._skills[skill.name] = skill

    def get(self, name: str) -> Skill:
        skill = self._skills.get(name)
        if skill is None:
            raise SkillNotFoundError(name)
        return skill

    def find(self, names: list[str]) -> list[Skill]:
        return [self.get(name) for name in names]

    def list(self) -> list[Skill]:
        return list(self._skills.values())

    @classmethod
    def from_directory(cls, path: str) -> SkillCatalog:
        from pathlib import Path

        dir_path = Path(path)
        if not dir_path.exists():
            raise FileNotFoundError(f"Skills directory not found: {path}")

        skills = []
        for skill_dir in sorted(dir_path.iterdir()):
            if not skill_dir.is_dir():
                continue
            skill_file = skill_dir / "SKILL.md"
            if skill_file.exists():
                try:
                    skills.append(Skill.from_file(str(skill_file)))
                except SkillValidationError as e:
                    logger.warning("Skipping invalid skill in %s: %s", skill_dir, e)

        return cls(skills)


def build_skills_preamble(catalog: SkillCatalog) -> str:
    """Build a system-prompt section listing available skills."""
    skills = catalog.list()
    lines = [
        "\n\n## Skills",
        "",
        "You have skills available. To activate a skill, call the `use_skill` "
        "tool with the skill name. The skill returns detailed instructions "
        "for completing the task — follow them using your available tools.",
        "",
        "Available skills:",
    ]
    for skill in skills:
        lines.append(f"- {skill.name}: {skill.description}")
    return "\n".join(lines)


def build_use_skill(catalog: SkillCatalog) -> task:
    @task
    async def use_skill(name: str) -> str:
        """Activates a skill by name. Returns the skill's full instructions."""
        skill = catalog.get(name)
        return skill.instructions

    return use_skill
