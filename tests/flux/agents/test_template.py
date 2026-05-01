"""Tests for agent chat workflow template."""

from __future__ import annotations

from pathlib import Path

import pytest

from flux.agents.template import _materialize_skills_bundle, agent_chat


def test_template_exists():
    assert agent_chat is not None


def test_template_is_workflow():
    assert hasattr(agent_chat, "__wrapped__") or callable(agent_chat)


def test_materialize_skills_bundle_writes_relative_paths(tmp_path: Path):
    bundle = {"my-skill": {"SKILL.md": "hello", "nested/file.md": "world"}}
    _materialize_skills_bundle(tmp_path, bundle)
    assert (tmp_path / "SKILL.md").read_text() == "hello"
    assert (tmp_path / "nested/file.md").read_text() == "world"


def test_materialize_skills_bundle_rejects_absolute_path(tmp_path: Path):
    bundle = {"my-skill": {"/etc/cron.d/pwn": "rooted"}}
    with pytest.raises(ValueError, match="unsafe file path"):
        _materialize_skills_bundle(tmp_path, bundle)


def test_materialize_skills_bundle_rejects_dotdot(tmp_path: Path):
    bundle = {"my-skill": {"../../../etc/passwd": "rooted"}}
    with pytest.raises(ValueError, match="unsafe file path"):
        _materialize_skills_bundle(tmp_path, bundle)


def test_materialize_skills_bundle_rejects_escape_via_symlink_resolution(tmp_path: Path):
    inner = tmp_path / "bundle"
    inner.mkdir()
    sibling = tmp_path / "outside"
    sibling.mkdir()
    (inner / "link").symlink_to(sibling)
    bundle = {"my-skill": {"link/escape.txt": "rooted"}}
    with pytest.raises(ValueError, match="escapes bundle root"):
        _materialize_skills_bundle(inner, bundle)


def test_materialize_skills_bundle_rejects_non_mapping_files(tmp_path: Path):
    bundle = {"my-skill": "not a dict"}
    with pytest.raises(ValueError, match="must be a mapping"):
        _materialize_skills_bundle(tmp_path, bundle)
