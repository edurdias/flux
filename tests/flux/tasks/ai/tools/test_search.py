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
def search_tools(config):
    from flux.tasks.ai.tools.search import build_search_tools

    return {t.func.__name__: t for t in build_search_tools(config)}


def _run(coro):
    async def _wrapper():
        ctx = ExecutionContext(workflow_id="test", workflow_namespace="default", workflow_name="test")
        token = ExecutionContext.set(ctx)
        try:
            return await coro
        finally:
            ExecutionContext.reset(token)

    return asyncio.run(_wrapper())


# --- find_files ---


def test_find_files_glob(search_tools, tmp_path):
    (tmp_path / "a.py").touch()
    (tmp_path / "b.py").touch()
    (tmp_path / "c.txt").touch()
    result = _run(search_tools["find_files"](pattern="*.py"))
    assert result["status"] == "ok"
    assert result["total"] == 2
    assert "a.py" in result["matches"]
    assert "b.py" in result["matches"]


def test_find_files_recursive(search_tools, tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "deep.py").touch()
    result = _run(search_tools["find_files"](pattern="**/*.py"))
    assert result["status"] == "ok"
    assert any("deep.py" in m for m in result["matches"])


def test_find_files_in_subpath(search_tools, tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").touch()
    (tmp_path / "other.py").touch()
    result = _run(search_tools["find_files"](pattern="*.py", path="src"))
    assert result["status"] == "ok"
    assert result["total"] == 1


def test_find_files_no_matches(search_tools):
    result = _run(search_tools["find_files"](pattern="*.xyz"))
    assert result["status"] == "ok"
    assert result["total"] == 0


def test_find_files_path_escape(search_tools):
    result = _run(search_tools["find_files"](pattern="*.py", path="../"))
    assert result["status"] == "error"
    assert "escape" in result["error"].lower()


def test_find_files_glob_escape_filtered(search_tools, tmp_path):
    result = _run(search_tools["find_files"](pattern="../*"))
    assert result["status"] == "ok"
    for m in result["matches"]:
        assert ".." not in m


def test_find_files_truncates_by_list_count(tmp_path):
    config = SystemToolsConfig(
        workspace=tmp_path,
        timeout=30,
        blocklist=[],
        max_output_chars=50,
    )
    from flux.tasks.ai.tools.search import build_search_tools

    for i in range(20):
        (tmp_path / f"file_{i:03d}.py").touch()
    tools = {t.func.__name__: t for t in build_search_tools(config)}
    result = _run(tools["find_files"](pattern="*.py"))
    assert result["status"] == "ok"
    assert result["total"] == 20
    assert result["truncated"] is True
    assert len(result["matches"]) < 20
    # All returned matches are valid strings
    for m in result["matches"]:
        assert isinstance(m, str)


# --- grep ---


def test_grep_finds_matches(search_tools, tmp_path):
    (tmp_path / "code.py").write_text("def hello():\n    return 'world'\n")
    result = _run(search_tools["grep"](pattern="def hello"))
    assert result["status"] == "ok"
    assert result["total"] == 1
    assert result["matches"][0]["file"] == "code.py"
    assert result["matches"][0]["line"] == 1
    assert "def hello" in result["matches"][0]["content"]


def test_grep_regex(search_tools, tmp_path):
    (tmp_path / "data.txt").write_text("foo123\nbar456\nfoo789\n")
    result = _run(search_tools["grep"](pattern=r"foo\d+"))
    assert result["status"] == "ok"
    assert result["total"] == 2


def test_grep_include_filter(search_tools, tmp_path):
    (tmp_path / "a.py").write_text("match here\n")
    (tmp_path / "b.txt").write_text("match here\n")
    result = _run(search_tools["grep"](pattern="match", include="*.py"))
    assert result["status"] == "ok"
    assert result["total"] == 1
    assert result["matches"][0]["file"] == "a.py"


def test_grep_no_matches(search_tools, tmp_path):
    (tmp_path / "empty.txt").write_text("nothing relevant\n")
    result = _run(search_tools["grep"](pattern="zzz_not_here"))
    assert result["status"] == "ok"
    assert result["total"] == 0


def test_grep_path_escape(search_tools):
    result = _run(search_tools["grep"](pattern="x", path="../"))
    assert result["status"] == "error"
    assert "escape" in result["error"].lower()


def test_grep_in_subpath(search_tools, tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("target line\n")
    (tmp_path / "other.py").write_text("target line\n")
    result = _run(search_tools["grep"](pattern="target", path="src"))
    assert result["status"] == "ok"
    assert result["total"] == 1


def test_grep_truncates_matches_list(tmp_path):
    config = SystemToolsConfig(
        workspace=tmp_path,
        timeout=30,
        blocklist=[],
        max_output_chars=100,
    )
    from flux.tasks.ai.tools.search import build_search_tools

    lines = "\n".join(f"match_line_{i}" for i in range(50))
    (tmp_path / "big.txt").write_text(lines)
    tools = {t.func.__name__: t for t in build_search_tools(config)}
    result = _run(tools["grep"](pattern="match_line"))
    assert result["status"] == "ok"
    assert result["total"] == 50
    assert result["truncated"] is True
    assert len(result["matches"]) < 50
    # All returned matches have the expected structure
    for m in result["matches"]:
        assert "file" in m and "line" in m and "content" in m
