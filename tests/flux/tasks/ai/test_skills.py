from __future__ import annotations

import logging
import os

from flux.errors import ExecutionError
import pytest

from flux.tasks.ai.skills import (
    Skill,
    SkillCatalog,
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


def test_skill_construction():
    skill = Skill(name="my-skill", description="A skill.", instructions="Do the thing.")
    assert skill.name == "my-skill"
    assert skill.description == "A skill."
    assert skill.instructions == "Do the thing."
    assert skill.allowed_tools == []
    assert skill.metadata == {}


def test_skill_with_all_fields():
    skill = Skill(
        name="my-skill",
        description="A skill.",
        instructions="Do the thing.",
        allowed_tools=["tool-a", "tool-b"],
        metadata={"author": "acme"},
    )
    assert skill.allowed_tools == ["tool-a", "tool-b"]
    assert skill.metadata == {"author": "acme"}


def test_skill_rejects_missing_name():
    with pytest.raises(SkillValidationError):
        Skill(name="", description="A skill.", instructions="Do the thing.")


def test_skill_rejects_missing_description():
    with pytest.raises(SkillValidationError):
        Skill(name="my-skill", description="", instructions="Do the thing.")


def test_skill_rejects_missing_instructions():
    with pytest.raises(SkillValidationError):
        Skill(name="my-skill", description="A skill.", instructions="")


def test_skill_rejects_uppercase_name():
    with pytest.raises(SkillValidationError):
        Skill(name="MySkill", description="A skill.", instructions="Do the thing.")


def test_skill_rejects_name_starting_with_hyphen():
    with pytest.raises(SkillValidationError):
        Skill(name="-my-skill", description="A skill.", instructions="Do the thing.")


def test_skill_rejects_name_ending_with_hyphen():
    with pytest.raises(SkillValidationError):
        Skill(name="my-skill-", description="A skill.", instructions="Do the thing.")


def test_skill_rejects_consecutive_hyphens():
    with pytest.raises(SkillValidationError):
        Skill(name="my--skill", description="A skill.", instructions="Do the thing.")


def test_skill_rejects_name_over_64_chars():
    with pytest.raises(SkillValidationError):
        Skill(name="a" * 65, description="A skill.", instructions="Do the thing.")


def test_skill_repr():
    skill = Skill(name="my-skill", description="A skill.", instructions="Do the thing.")
    assert repr(skill) == "Skill(name='my-skill')"


FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "skills_fixtures")


def test_skill_from_file():
    path = os.path.join(FIXTURES_DIR, "researcher", "SKILL.md")
    skill = Skill.from_file(path)
    assert skill.name == "researcher"
    assert skill.description == "Deep research on a topic using web sources."
    assert "search_web" in skill.allowed_tools
    assert "read_url" in skill.allowed_tools
    assert skill.metadata["author"] == "acme-corp"
    assert skill.metadata["version"] == "1.0"
    assert "Research the given topic thoroughly." in skill.instructions


def test_skill_from_file_minimal():
    path = os.path.join(FIXTURES_DIR, "minimal", "SKILL.md")
    skill = Skill.from_file(path)
    assert skill.name == "minimal"
    assert skill.description == "A minimal skill."
    assert skill.allowed_tools == []
    assert skill.metadata == {}
    assert "Do the thing." in skill.instructions


def test_skill_from_file_name_mismatch_warns(caplog):
    path = os.path.join(FIXTURES_DIR, "bad-name", "SKILL.md")
    with caplog.at_level(logging.WARNING, logger="flux.skills"):
        skill = Skill.from_file(path)
    assert skill.name == "wrong-name-here"
    assert "wrong-name-here" in caplog.text
    assert "bad-name" in caplog.text


def test_skill_from_file_missing_description():
    path = os.path.join(FIXTURES_DIR, "missing-desc", "SKILL.md")
    with pytest.raises(SkillValidationError):
        Skill.from_file(path)


def test_skill_from_file_empty_body():
    path = os.path.join(FIXTURES_DIR, "empty-body", "SKILL.md")
    with pytest.raises(SkillValidationError):
        Skill.from_file(path)


def test_skill_from_file_not_found():
    with pytest.raises(FileNotFoundError):
        Skill.from_file("/nonexistent/path/SKILL.md")


def _make_skill(name: str) -> Skill:
    return Skill(name=name, description=f"Skill {name}.", instructions=f"Do {name}.")


def test_catalog_from_list():
    s1 = _make_skill("alpha")
    s2 = _make_skill("beta")
    catalog = SkillCatalog([s1, s2])
    assert len(catalog.list()) == 2


def test_catalog_empty():
    catalog = SkillCatalog([])
    assert catalog.list() == []


def test_catalog_get():
    s1 = _make_skill("alpha")
    catalog = SkillCatalog([s1])
    assert catalog.get("alpha") is s1


def test_catalog_get_not_found():
    catalog = SkillCatalog([])
    with pytest.raises(SkillNotFoundError):
        catalog.get("missing")


def test_catalog_find():
    s1 = _make_skill("alpha")
    s2 = _make_skill("beta")
    s3 = _make_skill("gamma")
    catalog = SkillCatalog([s1, s2, s3])
    found = catalog.find(["alpha", "gamma"])
    assert len(found) == 2
    assert found[0].name == "alpha"
    assert found[1].name == "gamma"


def test_catalog_find_missing():
    s1 = _make_skill("alpha")
    catalog = SkillCatalog([s1])
    with pytest.raises(SkillNotFoundError):
        catalog.find(["alpha", "missing"])


def test_catalog_register():
    catalog = SkillCatalog([])
    s1 = _make_skill("alpha")
    catalog.register(s1)
    assert catalog.get("alpha") is s1


def test_catalog_register_duplicate():
    s1 = _make_skill("alpha")
    catalog = SkillCatalog([s1])
    with pytest.raises(SkillCatalogError):
        catalog.register(_make_skill("alpha"))


def test_catalog_init_duplicate():
    s1 = _make_skill("alpha")
    s2 = _make_skill("alpha")
    with pytest.raises(SkillCatalogError):
        SkillCatalog([s1, s2])


def test_catalog_from_directory():
    catalog = SkillCatalog.from_directory(FIXTURES_DIR)
    names = {s.name for s in catalog.list()}
    assert "researcher" in names
    assert "minimal" in names


def test_catalog_from_directory_not_found():
    with pytest.raises(FileNotFoundError):
        SkillCatalog.from_directory("/nonexistent/path")


def test_catalog_from_directory_empty(tmp_path):
    catalog = SkillCatalog.from_directory(str(tmp_path))
    assert catalog.list() == []
