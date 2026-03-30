from __future__ import annotations

import datetime
import stat
from typing import TYPE_CHECKING

from flux.task import task

from flux.tasks.ai.tools.system_tools import resolve_path, truncate_output

if TYPE_CHECKING:
    from flux.tasks.ai.tools.system_tools import SystemToolsConfig


def build_file_tools(config: SystemToolsConfig) -> list:
    @task
    async def read_file(path: str, offset: int = 0, limit: int = 0) -> dict:
        """Read the contents of a file."""
        try:
            resolved = resolve_path(config, path)
        except ValueError as e:
            return {"status": "error", "error": str(e)}

        if not resolved.is_file():
            return {"status": "error", "error": f"file not found: {path}"}

        text = resolved.read_text(errors="replace")
        lines = text.splitlines(keepends=True)
        total_lines = len(lines)

        if offset or limit:
            end = offset + limit if limit else len(lines)
            lines = lines[offset:end]
            text = "".join(lines)

        content, truncated = truncate_output(text, config.max_output_chars)
        result = {
            "status": "ok",
            "path": path,
            "content": content,
            "lines": total_lines,
        }
        if truncated:
            result["truncated"] = True
            result["total_chars"] = len(text)
        return result

    @task
    async def write_file(path: str, content: str, create_dirs: bool = True) -> dict:
        """Create or overwrite a file."""
        try:
            resolved = resolve_path(config, path)
        except ValueError as e:
            return {"status": "error", "error": str(e)}

        created = not resolved.exists()

        if create_dirs:
            resolved.parent.mkdir(parents=True, exist_ok=True)
        elif not resolved.parent.exists():
            return {
                "status": "error",
                "error": f"parent directory does not exist: {resolved.parent}",
            }

        resolved.write_text(content)
        return {
            "status": "ok",
            "path": path,
            "bytes_written": len(content.encode()),
            "created": created,
        }

    @task
    async def edit_file(
        path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> dict:
        """Apply a search-and-replace edit to a file."""
        try:
            resolved = resolve_path(config, path)
        except ValueError as e:
            return {"status": "error", "error": str(e)}

        if not resolved.is_file():
            return {"status": "error", "error": f"file not found: {path}"}

        text = resolved.read_text(errors="replace")
        count = text.count(old_string)

        if count == 0:
            return {"status": "error", "error": f"old_string not found in {path}"}

        if count > 1 and not replace_all:
            return {
                "status": "error",
                "error": f"old_string found {count} times in {path}. Use replace_all=True or provide a more specific string.",
            }

        if replace_all:
            new_text = text.replace(old_string, new_string)
        else:
            new_text = text.replace(old_string, new_string, 1)

        resolved.write_text(new_text)
        replacements = count if replace_all else 1
        return {"status": "ok", "path": path, "replacements": replacements}

    @task
    async def file_info(path: str) -> dict:
        """Get metadata about a file or directory."""
        try:
            resolved = resolve_path(config, path)
        except ValueError as e:
            return {"status": "error", "error": str(e)}

        if not resolved.exists():
            return {"status": "error", "error": f"not found: {path}"}

        st = resolved.stat()
        file_type = "directory" if resolved.is_dir() else "file"
        mode = stat.filemode(st.st_mode)
        modified = datetime.datetime.fromtimestamp(
            st.st_mtime,
            tz=datetime.timezone.utc,
        ).isoformat()

        return {
            "status": "ok",
            "path": path,
            "type": file_type,
            "size": st.st_size,
            "modified": modified,
            "permissions": mode,
        }

    return [read_file, write_file, edit_file, file_info]
