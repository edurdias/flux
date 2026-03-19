from __future__ import annotations

from flux.errors import ExecutionError
from flux.tasks.ai.skills import (
    SkillCatalogError,
    SkillNotFoundError,
    SkillValidationError,
)


def test_skill_validation_error_is_value_error():
    err = SkillValidationError("bad input")
    assert isinstance(err, ValueError)


def test_skill_catalog_error_is_value_error():
    err = SkillCatalogError("duplicate")
    assert isinstance(err, ValueError)


def test_skill_not_found_error_is_execution_error():
    err = SkillNotFoundError("my-skill")
    assert isinstance(err, ExecutionError)


def test_skill_not_found_error_message():
    err = SkillNotFoundError("my-skill")
    assert err.message == "Skill 'my-skill' not found."
    assert err._skill_name == "my-skill"
