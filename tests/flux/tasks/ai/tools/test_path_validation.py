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


class TestSymlinkContainment:
    """``resolve_path`` validates only the path it is handed. Tools that walk
    the tree must re-check each entry they reach, because a symlink inside the
    workspace resolves outside it — ``find_files`` and ``read_file`` do, so
    ``grep`` and ``directory_tree`` must not be the way out."""

    @pytest.fixture
    def config(self, tmp_path):
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secrets.env").write_text("API_TOKEN=sk-not-yours\n")

        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "own.txt").write_text("nothing interesting\n")
        try:
            (ws / "link.env").symlink_to(outside / "secrets.env")
            (ws / "dirlink").symlink_to(outside, target_is_directory=True)
        except OSError:
            pytest.skip("symlinks not supported")
        return SystemToolsConfig(
            workspace=ws,
            timeout=30,
            blocklist=[],
            max_output_chars=100_000,
        )

    @staticmethod
    async def _call(tool, *args):
        from flux.domain.execution_context import ExecutionContext

        ctx = ExecutionContext(
            workflow_id="test",
            workflow_namespace="default",
            workflow_name="test",
        )
        token = ExecutionContext.set(ctx)
        try:
            return await tool(*args)
        finally:
            ExecutionContext.reset(token)

    @staticmethod
    def _tools(builder, config):
        return {t.func.__name__: t for t in builder(config)}

    async def test_grep_does_not_follow_symlink_out_of_workspace(self, config):
        from flux.tasks.ai.tools.search import build_search_tools

        tools = self._tools(build_search_tools, config)
        result = await self._call(tools["grep"], "API_TOKEN")

        assert result["status"] == "ok"
        assert result["matches"] == []
        assert "sk-not-yours" not in str(result)

    async def test_directory_tree_does_not_descend_symlinked_dir(self, config):
        from flux.tasks.ai.tools.directory import build_directory_tools

        tools = self._tools(build_directory_tools, config)
        result = await self._call(tools["directory_tree"], "")

        assert result["status"] == "ok"
        assert "secrets.env" not in result["tree"]

    async def test_grep_still_reads_real_workspace_files(self, config):
        from flux.tasks.ai.tools.search import build_search_tools

        tools = self._tools(build_search_tools, config)
        result = await self._call(tools["grep"], "nothing interesting")

        assert result["status"] == "ok"
        assert [m["file"] for m in result["matches"]] == ["own.txt"]

    async def test_list_directory_hides_entries_that_escape(self, config):
        """``is_dir()``/``stat()`` follow symlinks here too: without the same
        per-entry check the listing reports the type and size of files outside
        the workspace."""
        from flux.tasks.ai.tools.directory import build_directory_tools

        tools = self._tools(build_directory_tools, config)
        result = await self._call(tools["list_directory"], "")

        assert result["status"] == "ok"
        assert [e["name"] for e in result["entries"]] == ["own.txt"]

    async def test_grep_paths_are_relative_when_workspace_is_symlinked(self, tmp_path):
        """A workspace path that itself contains a symlink (``/tmp`` on macOS,
        a symlinked checkout) must still yield paths relative to it — the walk
        starts from the *resolved* root, so relpath needs the resolved base."""
        from flux.tasks.ai.tools.search import build_search_tools

        real = tmp_path / "real_ws"
        real.mkdir()
        (real / "own.txt").write_text("nothing interesting\n")
        linked = tmp_path / "linked_ws"
        try:
            linked.symlink_to(real, target_is_directory=True)
        except OSError:
            pytest.skip("symlinks not supported")

        config = SystemToolsConfig(
            workspace=linked,
            timeout=30,
            blocklist=[],
            max_output_chars=100_000,
        )
        tools = self._tools(build_search_tools, config)
        result = await self._call(tools["grep"], "nothing interesting")

        assert [m["file"] for m in result["matches"]] == ["own.txt"]
