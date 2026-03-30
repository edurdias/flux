from __future__ import annotations

from typing import TYPE_CHECKING

from flux.task import task

from flux.tasks.ai.tools.system_tools import resolve_path, truncate_output

if TYPE_CHECKING:
    from flux.tasks.ai.tools.system_tools import SystemToolsConfig


def build_directory_tools(config: SystemToolsConfig) -> list[task]:
    @task
    async def list_directory(path: str = "") -> dict:
        """List the contents of a directory."""
        try:
            resolved = resolve_path(config, path)
        except ValueError as e:
            return {"status": "error", "error": str(e)}

        if not resolved.is_dir():
            return {"status": "error", "error": f"not a directory: {path}"}

        entries = []
        for entry in sorted(resolved.iterdir(), key=lambda e: e.name):
            info: dict[str, str | int] = {"name": entry.name}
            if entry.is_dir():
                info["type"] = "directory"
            else:
                info["type"] = "file"
                try:
                    info["size"] = entry.stat().st_size
                except OSError:
                    pass
            entries.append(info)

        return {
            "status": "ok",
            "path": path or ".",
            "entries": entries,
        }

    @task
    async def directory_tree(path: str = "", max_depth: int = 3) -> dict:
        """Get a recursive tree view of a directory."""
        try:
            resolved = resolve_path(config, path)
        except ValueError as e:
            return {"status": "error", "error": str(e)}

        if not resolved.is_dir():
            return {"status": "error", "error": f"not a directory: {path}"}

        lines = []
        file_count = 0
        dir_count = 0

        def _walk(current, prefix, depth):
            nonlocal file_count, dir_count
            if depth > max_depth:
                return
            try:
                entries = sorted(current.iterdir(), key=lambda e: (not e.is_dir(), e.name))
            except OSError:
                return
            for entry in entries:
                if entry.is_dir():
                    dir_count += 1
                    lines.append(f"{prefix}{entry.name}/")
                    _walk(entry, prefix + "  ", depth + 1)
                else:
                    file_count += 1
                    lines.append(f"{prefix}{entry.name}")

        root_name = path or "."
        lines.append(f"{root_name}/")
        _walk(resolved, "  ", 1)

        tree_str = "\n".join(lines)
        tree_output, was_truncated = truncate_output(tree_str, config.max_output_chars)

        result = {
            "status": "ok",
            "path": path or ".",
            "tree": tree_output,
            "files": file_count,
            "directories": dir_count,
        }
        if was_truncated:
            result["truncated"] = True
        return result

    return [list_directory, directory_tree]
