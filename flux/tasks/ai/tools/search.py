from __future__ import annotations

import fnmatch
import json
import os
import re
from typing import TYPE_CHECKING

from flux.task import task

from flux.tasks.ai.tools.system_tools import resolve_path, truncate_output

if TYPE_CHECKING:
    from flux.tasks.ai.tools.system_tools import SystemToolsConfig


def build_search_tools(config: SystemToolsConfig) -> list:
    @task
    async def find_files(pattern: str, path: str = "") -> dict:
        """Find files matching a glob pattern."""
        try:
            search_root = resolve_path(config, path)
        except ValueError as e:
            return {"status": "error", "error": str(e)}

        if not search_root.is_dir():
            return {"status": "error", "error": f"not a directory: {path}"}

        matches = []
        for match in sorted(
            search_root.rglob(pattern) if "**" in pattern else search_root.glob(pattern),
        ):
            rel = str(match.relative_to(config.workspace))
            matches.append(rel)

        serialized = json.dumps(matches)
        truncated_str, was_truncated = truncate_output(serialized, config.max_output_chars)

        result = {
            "status": "ok",
            "pattern": pattern,
            "matches": matches if not was_truncated else json.loads(truncated_str + '"]'),
            "total": len(matches),
        }
        if was_truncated:
            result["truncated"] = True
        return result

    @task
    async def grep(pattern: str, path: str = "", include: str = "") -> dict:
        """Search file contents by regex pattern."""
        try:
            search_root = resolve_path(config, path)
        except ValueError as e:
            return {"status": "error", "error": str(e)}

        if not search_root.is_dir():
            return {"status": "error", "error": f"not a directory: {path}"}

        try:
            regex = re.compile(pattern)
        except re.error as e:
            return {"status": "error", "error": f"invalid regex: {e}"}

        matches = []
        for root, _dirs, files in os.walk(search_root):
            for fname in sorted(files):
                if include and not fnmatch.fnmatch(fname, include):
                    continue
                fpath = os.path.join(root, fname)
                try:
                    with open(fpath, errors="replace") as f:
                        for line_num, line in enumerate(f, 1):
                            if regex.search(line):
                                rel = os.path.relpath(fpath, config.workspace)
                                matches.append(
                                    {
                                        "file": rel,
                                        "line": line_num,
                                        "content": line.rstrip("\n"),
                                    },
                                )
                except (OSError, UnicodeDecodeError):
                    continue

        serialized = json.dumps(matches)
        _, was_truncated = truncate_output(serialized, config.max_output_chars)

        result = {
            "status": "ok",
            "pattern": pattern,
            "matches": matches,
            "total": len(matches),
        }
        if was_truncated:
            result["truncated"] = True
        return result

    return [find_files, grep]
