from __future__ import annotations

import asyncio

import pytest

from flux.domain.execution_context import ExecutionContext
from flux.tasks.ai.tools.system_tools import SystemToolsConfig


@pytest.fixture
def config(tmp_path):
    return SystemToolsConfig(
        workspace=tmp_path,
        timeout=30,
        blocklist=[],
        max_output_chars=100_000,
    )


@pytest.fixture
def dir_tools(config):
    from flux.tasks.ai.tools.directory import build_directory_tools

    return {t.func.__name__: t for t in build_directory_tools(config)}


def _run(coro):
    async def _wrapper():
        ctx = ExecutionContext(workflow_id="test", workflow_namespace="default", workflow_name="test")
        token = ExecutionContext.set(ctx)
        try:
            return await coro
        finally:
            ExecutionContext.reset(token)

    return asyncio.run(_wrapper())


# --- list_directory ---


def test_list_directory_root(dir_tools, tmp_path):
    (tmp_path / "file.txt").write_text("hello")
    (tmp_path / "subdir").mkdir()
    result = _run(dir_tools["list_directory"](path=""))
    assert result["status"] == "ok"
    names = {e["name"] for e in result["entries"]}
    assert "file.txt" in names
    assert "subdir" in names


def test_list_directory_file_metadata(dir_tools, tmp_path):
    (tmp_path / "file.txt").write_text("12345")
    result = _run(dir_tools["list_directory"](path=""))
    entry = next(e for e in result["entries"] if e["name"] == "file.txt")
    assert entry["type"] == "file"
    assert entry["size"] == 5


def test_list_directory_subdir_type(dir_tools, tmp_path):
    (tmp_path / "sub").mkdir()
    result = _run(dir_tools["list_directory"](path=""))
    entry = next(e for e in result["entries"] if e["name"] == "sub")
    assert entry["type"] == "directory"


def test_list_directory_nested(dir_tools, tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "b.txt").touch()
    result = _run(dir_tools["list_directory"](path="a"))
    assert result["status"] == "ok"
    assert len(result["entries"]) == 1
    assert result["entries"][0]["name"] == "b.txt"


def test_list_directory_not_found(dir_tools):
    result = _run(dir_tools["list_directory"](path="nope"))
    assert result["status"] == "error"


def test_list_directory_path_escape(dir_tools):
    result = _run(dir_tools["list_directory"](path="../"))
    assert result["status"] == "error"
    assert "escape" in result["error"].lower()


# --- directory_tree ---


def test_directory_tree_basic(dir_tools, tmp_path):
    (tmp_path / "a.txt").touch()
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.txt").touch()
    result = _run(dir_tools["directory_tree"](path=""))
    assert result["status"] == "ok"
    assert result["files"] == 2
    assert result["directories"] == 1
    assert "a.txt" in result["tree"]
    assert "b.txt" in result["tree"]


def test_directory_tree_max_depth(dir_tools, tmp_path):
    (tmp_path / "l1").mkdir()
    (tmp_path / "l1" / "l2").mkdir()
    (tmp_path / "l1" / "l2" / "l3").mkdir()
    (tmp_path / "l1" / "l2" / "l3" / "deep.txt").touch()
    result = _run(dir_tools["directory_tree"](path="", max_depth=1))
    assert result["status"] == "ok"
    assert "deep.txt" not in result["tree"]


def test_directory_tree_path_escape(dir_tools):
    result = _run(dir_tools["directory_tree"](path="../"))
    assert result["status"] == "error"
    assert "escape" in result["error"].lower()


def test_directory_tree_not_found(dir_tools):
    result = _run(dir_tools["directory_tree"](path="missing"))
    assert result["status"] == "error"
