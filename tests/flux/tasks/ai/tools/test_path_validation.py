from __future__ import annotations


import pytest

from flux.tasks.ai.tools.system_tools import SystemToolsConfig, resolve_path


@pytest.fixture
def config(tmp_path):
    return SystemToolsConfig(
        workspace=tmp_path,
        timeout=30,
        blocklist=[],
        max_output_chars=100_000,
    )


def test_relative_path(config, tmp_path):
    (tmp_path / "file.txt").touch()
    result = resolve_path(config, "file.txt")
    assert result == tmp_path / "file.txt"


def test_nested_relative_path(config, tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "file.txt").touch()
    result = resolve_path(config, "sub/file.txt")
    assert result == tmp_path / "sub" / "file.txt"


def test_empty_path_resolves_to_workspace(config, tmp_path):
    result = resolve_path(config, "")
    assert result == tmp_path


def test_dot_resolves_to_workspace(config, tmp_path):
    result = resolve_path(config, ".")
    assert result == tmp_path


def test_parent_escape_rejected(config):
    with pytest.raises(ValueError, match="path escapes workspace boundary"):
        resolve_path(config, "../outside")


def test_double_parent_escape_rejected(config):
    with pytest.raises(ValueError, match="path escapes workspace boundary"):
        resolve_path(config, "../../outside")


def test_absolute_path_outside_rejected(config):
    with pytest.raises(ValueError, match="path escapes workspace boundary"):
        resolve_path(config, "/etc/passwd")


def test_absolute_path_inside_allowed(config, tmp_path):
    (tmp_path / "inside.txt").touch()
    result = resolve_path(config, str(tmp_path / "inside.txt"))
    assert result == tmp_path / "inside.txt"


def test_sneaky_escape_rejected(config):
    with pytest.raises(ValueError, match="path escapes workspace boundary"):
        resolve_path(config, "sub/../../outside")


def test_symlink_escape_rejected(config, tmp_path):
    target = tmp_path.parent / "outside_target"
    target.mkdir(exist_ok=True)
    link = tmp_path / "sneaky_link"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlinks not supported")
    with pytest.raises(ValueError, match="path escapes workspace boundary"):
        resolve_path(config, "sneaky_link")
