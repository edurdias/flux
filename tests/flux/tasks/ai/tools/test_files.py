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
def file_tools(config):
    from flux.tasks.ai.tools.files import build_file_tools

    return {t.func.__name__: t for t in build_file_tools(config)}


def _run(coro):
    async def _wrapper():
        ctx = ExecutionContext(workflow_id="test", workflow_name="test")
        token = ExecutionContext.set(ctx)
        try:
            return await coro
        finally:
            ExecutionContext.reset(token)

    return asyncio.run(_wrapper())


# --- read_file ---


def test_read_file(file_tools, tmp_path):
    (tmp_path / "hello.txt").write_text("line1\nline2\nline3\n")
    result = _run(file_tools["read_file"](path="hello.txt"))
    assert result["status"] == "ok"
    assert result["lines"] == 3
    assert "line1" in result["content"]


def test_read_file_with_offset_and_limit(file_tools, tmp_path):
    (tmp_path / "nums.txt").write_text("a\nb\nc\nd\ne\n")
    result = _run(file_tools["read_file"](path="nums.txt", offset=1, limit=2))
    assert result["status"] == "ok"
    assert "b" in result["content"]
    assert "c" in result["content"]
    assert "a" not in result["content"]


def test_read_file_not_found(file_tools):
    result = _run(file_tools["read_file"](path="missing.txt"))
    assert result["status"] == "error"


def test_read_file_path_escape(file_tools):
    result = _run(file_tools["read_file"](path="../outside.txt"))
    assert result["status"] == "error"
    assert "escape" in result["error"].lower()


def test_read_file_truncation(tmp_path):
    config = SystemToolsConfig(
        workspace=tmp_path,
        timeout=30,
        blocklist=[],
        max_output_chars=20,
    )
    from flux.tasks.ai.tools.files import build_file_tools

    tools = {t.func.__name__: t for t in build_file_tools(config)}
    (tmp_path / "big.txt").write_text("x" * 200)
    result = _run(tools["read_file"](path="big.txt"))
    assert result["status"] == "ok"
    assert result["truncated"] is True
    assert len(result["content"]) == 20


# --- write_file ---


def test_write_file_creates_new(file_tools, tmp_path):
    result = _run(file_tools["write_file"](path="new.txt", content="hello"))
    assert result["status"] == "ok"
    assert result["created"] is True
    assert result["bytes_written"] == 5
    assert (tmp_path / "new.txt").read_text() == "hello"


def test_write_file_overwrites(file_tools, tmp_path):
    (tmp_path / "exist.txt").write_text("old")
    result = _run(file_tools["write_file"](path="exist.txt", content="new"))
    assert result["status"] == "ok"
    assert result["created"] is False
    assert (tmp_path / "exist.txt").read_text() == "new"


def test_write_file_creates_dirs(file_tools, tmp_path):
    result = _run(file_tools["write_file"](path="sub/deep/file.txt", content="nested"))
    assert result["status"] == "ok"
    assert (tmp_path / "sub" / "deep" / "file.txt").read_text() == "nested"


def test_write_file_no_create_dirs(file_tools):
    result = _run(file_tools["write_file"](path="no/dir/file.txt", content="x", create_dirs=False))
    assert result["status"] == "error"


def test_write_file_path_escape(file_tools):
    result = _run(file_tools["write_file"](path="../escape.txt", content="bad"))
    assert result["status"] == "error"
    assert "escape" in result["error"].lower()


# --- edit_file ---


def test_edit_file_single_replace(file_tools, tmp_path):
    (tmp_path / "code.py").write_text("def foo():\n    return 1\n")
    result = _run(
        file_tools["edit_file"](path="code.py", old_string="return 1", new_string="return 2"),
    )
    assert result["status"] == "ok"
    assert result["replacements"] == 1
    assert "return 2" in (tmp_path / "code.py").read_text()


def test_edit_file_old_string_not_found(file_tools, tmp_path):
    (tmp_path / "code.py").write_text("def foo(): pass\n")
    result = _run(file_tools["edit_file"](path="code.py", old_string="not here", new_string="x"))
    assert result["status"] == "error"
    assert "not found" in result["error"].lower()


def test_edit_file_ambiguous_without_replace_all(file_tools, tmp_path):
    (tmp_path / "dup.txt").write_text("aaa\naaa\n")
    result = _run(file_tools["edit_file"](path="dup.txt", old_string="aaa", new_string="bbb"))
    assert result["status"] == "error"
    assert "2" in result["error"]


def test_edit_file_replace_all(file_tools, tmp_path):
    (tmp_path / "dup.txt").write_text("aaa\naaa\n")
    result = _run(
        file_tools["edit_file"](
            path="dup.txt",
            old_string="aaa",
            new_string="bbb",
            replace_all=True,
        ),
    )
    assert result["status"] == "ok"
    assert result["replacements"] == 2
    assert (tmp_path / "dup.txt").read_text() == "bbb\nbbb\n"


def test_edit_file_path_escape(file_tools):
    result = _run(file_tools["edit_file"](path="../x.txt", old_string="a", new_string="b"))
    assert result["status"] == "error"
    assert "escape" in result["error"].lower()


# --- file_info ---


def test_file_info_file(file_tools, tmp_path):
    f = tmp_path / "info.txt"
    f.write_text("hello")
    result = _run(file_tools["file_info"](path="info.txt"))
    assert result["status"] == "ok"
    assert result["type"] == "file"
    assert result["size"] == 5
    assert "modified" in result
    assert "permissions" in result


def test_file_info_directory(file_tools, tmp_path):
    (tmp_path / "subdir").mkdir()
    result = _run(file_tools["file_info"](path="subdir"))
    assert result["status"] == "ok"
    assert result["type"] == "directory"


def test_file_info_not_found(file_tools):
    result = _run(file_tools["file_info"](path="nope.txt"))
    assert result["status"] == "error"


def test_file_info_path_escape(file_tools):
    result = _run(file_tools["file_info"](path="../outside"))
    assert result["status"] == "error"
    assert "escape" in result["error"].lower()
